"""Интеграция личной оценки ML-2 в официальный поток демо-заявки."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from time import monotonic
from uuid import UUID

from sqlalchemy import select

from app.models import Application, ApplicationDiagnosis, ApplicationItem, ItemCheck
from app.models.enums import DrugCategory
from app.services.reference_seed import CATEGORIES
from app.services.ml2_classifier import ClassificationResult, classify_purchase, gigachat_completer
from app.services.ml2_clinicalrecs_source import (
    ClinicalSourceError,
    fetch_recommendation,
    fragments_for_inn,
    recommendations_for_diagnosis,
)
from app.services.official_explanation import gigachat_settings
from app.services.official_reference import OfficialIndex, canonical_code, name_key
from llm_rag.providers.base import ProviderError
from llm_rag.providers.gigachat import GigaChatProvider

_MAX_PAIRS = 8
_BUDGET_SECONDS = 25.0


def _code_for_diagnosis(index: OfficialIndex, diagnosis: str) -> str | None:
    prefix = re.match(r"^\s*([A-ZА-ЯЁ][0-9]{2}(?:\.[0-9]{1,4})?)\b", diagnosis, re.I)
    if prefix:
        code = canonical_code(prefix.group(1))
        valid_codes = index.valid_codes
        if (
            not valid_codes
            or code in valid_codes
            or ("." in code and code.split(".", 1)[0] in valid_codes)
            or any(value.startswith(code + ".") for value in valid_codes)
        ):
            return code
        return None
    codes = index.diagnoses.get(name_key(diagnosis), set())
    return next(iter(codes)) if len(codes) == 1 else None


def _citation_dict(citation) -> dict:
    return {
        "fragment_id": citation.fragment_id,
        "source_id": citation.source_id,
        "title": citation.title,
        "version": citation.version,
        "url": citation.url,
        "publisher": "Минздрав России",
        "source_type": "PROTOCOL",
        "origin": "official",
        "retrieved_at": datetime.now(UTC).isoformat(),
        "location": citation.location,
        "quote": citation.quote,
        "copy_url": citation.copy_url,
    }


def _result_dict(result: ClassificationResult, **extra) -> dict:
    return {
        "status": result.status.value,
        "assessment": result.assessment.value if result.assessment else None,
        "proposed_category": result.proposed_category,
        "reason": result.review_reason,
        "conditions": list(result.conditions),
        "missing_context": list(result.missing_context),
        "citations": [_citation_dict(row) for row in result.citations],
        "registry_citations": [_citation_dict(row) for row in result.registry_citations],
        **extra,
    }


def _dosage_check(dosage: str | None) -> dict:
    """Return a conservative comparison result for the current claim schema.

    The workbook supplies a product dosage/strength, not the prescribed regimen.
    Without frequency, duration, route and patient-specific parameters, neither
    the model nor deterministic code can claim the purchase dose is appropriate.
    """
    if dosage and dosage.strip():
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": (
                "В заявке указана дозировка препарата, но нет назначенной схемы; "
                "сравнить её с клинической рекомендацией нельзя."
            ),
            "missing_data": [
                "Назначенная разовая доза",
                "Частота приёма",
                "Длительность приёма",
                "Путь введения",
            ],
        }
    return {
        "status": "INSUFFICIENT_DATA",
        "reason": "В заявке не указана дозировка и назначенная схема приёма.",
        "missing_data": [
            "Дозировка препарата",
            "Назначенная разовая доза",
            "Частота приёма",
            "Длительность приёма",
            "Путь введения",
        ],
    }


def classify_official_application(
    sessions,
    application_id: UUID | str,
    run_id: str,
    index: OfficialIndex,
) -> dict:
    """Сохранить предварительную категорию и её основание для следующего этапа."""

    with sessions() as session:
        application = session.get(Application, application_id)
        if application is None or (application.demo_meta or {}).get("analysis_run_id") != run_id:
            return {"attempted": False, "reason": "ANALYSIS_RUN_CHANGED", "pairs": 0}
        diagnoses = {
            row.id: row
            for row in session.scalars(
                select(ApplicationDiagnosis).where(
                    ApplicationDiagnosis.application_id == application_id
                )
            )
        }
        items = {
            row.id: row
            for row in session.scalars(
                select(ApplicationItem).where(ApplicationItem.application_id == application_id)
            )
        }
        checks = list(
            session.scalars(
                select(ItemCheck)
                .join(ApplicationItem)
                .where(ApplicationItem.application_id == application_id)
                .order_by(ApplicationItem.row_number, ItemCheck.id)
            )
        )
        item_blocks = (application.demo_meta or {}).get("item_blocks", {})
        diagnosis_blocks = (application.demo_meta or {}).get("diagnosis_blocks", {})
        block_diagnoses = {diagnosis_blocks.get(str(diag_id)): diag_id for diag_id in diagnoses}
        records = []
        for check in checks:
            item = items.get(check.item_id)
            diagnosis = diagnoses.get(check.diagnosis_id)
            if item is None or diagnosis is None:
                continue
            block = item_blocks.get(str(item.row_number))
            if block is not None and block_diagnoses.get(block) != diagnosis.id:
                continue
            records.append(
                (
                    check.id,
                    item.id,
                    item.drug_name or "",
                    item.dosage,
                    diagnosis.name,
                    item.mnn,
                    check.category is not None and not (check.evidence or {}).get("ml2_owns_category"),
                )
            )

    if not records:
        return {"attempted": False, "reason": "NO_PAIRS", "pairs": 0}

    # Prepare source fragments before opening the model connection. The only
    # external content sent to GigaChat is public recommendation text plus
    # diagnosis and medicine names from an explicitly synthetic demo workbook.
    prepared = []
    tokens: int | None = None
    model_calls = 0
    model_successes = 0
    for record in records:
        check_id, item_id, trade_name, dosage, diagnosis, inn, has_rule = record
        if not inn:
            prepared.append(
                (
                    check_id,
                    None,
                    {
                        "status": "INN_UNRESOLVED",
                        "assessment": None,
                        "proposed_category": None,
                        "reason": "В официальном снимке для этой строки не определено МНН.",
                        "conditions": [],
                        "missing_context": ["МНН"],
                        "citations": [],
                        "registry_citations": [],
                    },
                )
            )
            continue
        code = _code_for_diagnosis(index, diagnosis)
        if not code:
            prepared.append(
                (
                    check_id,
                    None,
                    {
                        "status": "NEEDS_CONTEXT",
                        "assessment": None,
                        "proposed_category": None,
                        "reason": "Диагноз не удалось однозначно сопоставить с кодом МКБ-10.",
                        "conditions": [],
                        "missing_context": ["Код МКБ-10"],
                        "citations": [],
                        "registry_citations": [],
                    },
                )
            )
            continue
        try:
            candidates = recommendations_for_diagnosis(code)
        except (OSError, ValueError):
            prepared.append(
                (
                    check_id,
                    None,
                    {
                        "status": "SOURCE_ERROR",
                        "assessment": None,
                        "proposed_category": None,
                        "reason": "Каталог клинических рекомендаций временно недоступен.",
                        "conditions": [],
                        "missing_context": [],
                        "citations": [],
                        "registry_citations": [],
                    },
                )
            )
            continue
        if len(candidates) != 1:
            reason = (
                "Для кода МКБ-10 не найден действующий документ Минздрава."
                if not candidates
                else "По коду найдено несколько рекомендаций; нужен возраст или выбор документа."
            )
            prepared.append(
                (
                    check_id,
                    None,
                    {
                        "status": "NEEDS_CONTEXT",
                        "assessment": None,
                        "proposed_category": None,
                        "reason": reason,
                        "conditions": [],
                        "missing_context": []
                        if not candidates
                        else ["Возрастная группа", "Редакция КР"],
                        "candidates": [
                            {
                                "title": c.title,
                                "edition_id": c.edition_id,
                                "population": c.population,
                                "page_url": c.page_url,
                            }
                            for c in candidates[:10]
                        ],
                        "citations": [],
                        "registry_citations": [],
                    },
                )
            )
            continue
        candidate = candidates[0]
        if candidate.cache_state == "STALE_CACHE":
            prepared.append(
                (
                    check_id,
                    None,
                    {
                        "status": "NEEDS_REVIEW",
                        "assessment": None,
                        "proposed_category": None,
                        "reason": (
                            "Каталог рекомендаций не удалось обновить; "
                            "резервная копия устарела и не подходит для классификации."
                        ),
                        "conditions": [],
                        "missing_context": ["Актуальность каталога КР"],
                        "candidates": [
                            {
                                "title": candidate.title,
                                "edition_id": candidate.edition_id,
                                "population": candidate.population,
                                "page_url": candidate.page_url,
                            }
                        ],
                        "citations": [],
                        "registry_citations": [],
                    },
                )
            )
            continue
        if candidate.population in {"Взрослые", "Дети"}:
            prepared.append(
                (
                    check_id,
                    None,
                    {
                        "status": "NEEDS_CONTEXT",
                        "assessment": None,
                        "proposed_category": None,
                        "reason": (
                            "Рекомендация ограничена возрастной группой; "
                            "возраст пациента не передан."
                        ),
                        "conditions": [],
                        "missing_context": ["Возрастная группа пациента"],
                        "candidates": [
                            {
                                "title": candidate.title,
                                "edition_id": candidate.edition_id,
                                "population": candidate.population,
                                "page_url": candidate.page_url,
                            }
                        ],
                        "citations": [],
                        "registry_citations": [],
                    },
                )
            )
            continue
        try:
            cached = fetch_recommendation(candidate.page_url)
            recommendation = cached.recommendation
            if (
                recommendation.edition_id != candidate.edition_id
                or recommendation.primary_url != candidate.primary_url
                or not recommendation.is_current_ministry_copy
                or (
                    code not in recommendation.icd10
                    and code.split(".", 1)[0] not in recommendation.icd10
                )
            ):
                raise ClinicalSourceError("Каталог и страница рекомендации не совпали.")
            fragments = fragments_for_inn(recommendation, inn)
        except (OSError, ValueError):
            prepared.append(
                (
                    check_id,
                    None,
                    {
                        "status": "SOURCE_ERROR",
                        "assessment": None,
                        "proposed_category": None,
                        "reason": (
                            "Не удалось проверить карточку и раздел лечения "
                            "выбранной редакции КР."
                        ),
                        "conditions": [],
                        "missing_context": [],
                        "citations": [],
                        "registry_citations": [],
                        "recommendation": {
                            "edition_id": candidate.edition_id,
                            "page_url": candidate.page_url,
                        },
                    },
                )
            )
            continue
        if cached.cache_state == "STALE_FALLBACK":
            prepared.append(
                (
                    check_id,
                    None,
                    {
                        "status": "NEEDS_REVIEW",
                        "assessment": None,
                        "proposed_category": None,
                        "reason": (
                            "Страница КР не обновлена; устаревшая копия не подходит "
                            "для классификации."
                        ),
                        "conditions": [],
                        "missing_context": ["Актуальность редакции КР"],
                        "citations": [],
                        "registry_citations": [],
                        "recommendation": {
                            "edition_id": candidate.edition_id,
                            "version": recommendation.version,
                            "cache_state": cached.cache_state,
                            "page_url": recommendation.page_url,
                        },
                    },
                )
            )
            continue
        if not fragments:
            prepared.append(
                (
                    check_id,
                    None,
                    {
                        "status": "NO_EVIDENCE",
                        "assessment": "NOT_FOUND",
                        "proposed_category": None,
                        "reason": (
                            "МНН не найдено в извлечённых блоках раздела лечения; "
                            "это не категория В."
                        ),
                        "conditions": [],
                        "missing_context": [],
                        "citations": [],
                        "registry_citations": [],
                        "recommendation": {
                            "edition_id": candidate.edition_id,
                            "version": recommendation.version,
                            "cache_state": cached.cache_state,
                            "page_url": recommendation.page_url,
                        },
                    },
                )
            )
            continue
        if cached.cache_state == "STALE_FALLBACK":
            prepared.append(
                (
                    check_id,
                    None,
                    {
                        "status": "NEEDS_REVIEW",
                        "assessment": None,
                        "proposed_category": None,
                        "reason": (
                            "Страница КР недоступна для обновления; "
                            "резервная копия устарела и не подходит для классификации."
                        ),
                        "conditions": [],
                        "missing_context": ["Актуальность редакции КР"],
                        "citations": [],
                        "registry_citations": [],
                        "recommendation": {
                            "edition_id": candidate.edition_id,
                            "version": recommendation.version,
                            "cache_state": cached.cache_state,
                            "page_url": recommendation.page_url,
                        },
                    },
                )
            )
            continue
        payload = (trade_name, dosage, diagnosis, code, item_id, fragments)
        metadata = {
            "item_id": str(item_id),
            "diagnosis_code": code,
            "recommendation": {
                "edition_id": candidate.edition_id,
                "version": recommendation.version,
                "cache_state": cached.cache_state,
                "page_url": recommendation.page_url,
                "primary_url": recommendation.primary_url,
                "population": recommendation.population,
            },
            "approved_rule_preserved": has_rule,
        }
        prepared.append((check_id, payload, metadata))

    pending = [(check_id, payload, metadata) for check_id, payload, metadata in prepared if payload]
    for check_id, _, _ in pending[_MAX_PAIRS:]:
        prepared.append(
            (
                check_id,
                None,
                {
                    "status": "NEEDS_REVIEW",
                    "assessment": None,
                    "proposed_category": None,
                    "reason": "Лимит пар GigaChat на одну заявку достигнут.",
                    "conditions": [],
                    "missing_context": [],
                    "citations": [],
                    "registry_citations": [],
                },
            )
        )
    pending = pending[:_MAX_PAIRS]
    config = gigachat_settings()
    credentials, access_token = config["GIGACHAT_CREDENTIALS"], config["GIGACHAT_ACCESS_TOKEN"]
    if pending and bool(credentials) != bool(access_token):
        deadline = monotonic() + _BUDGET_SECONDS
        try:
            with GigaChatProvider(
                credentials,
                access_token=access_token,
                enabled=True,
                model=config["GIGACHAT_MODEL"] or "GigaChat-2",
                scope="GIGACHAT_API_B2B",
                ca_bundle=config["GIGACHAT_CA_BUNDLE"] or None,
                timeout=15.0,
            ) as provider:
                complete_json = gigachat_completer(provider)
                for check_id, payload, metadata in pending:
                    if monotonic() >= deadline - 2:
                        prepared.append(
                            (
                                check_id,
                                None,
                                {
                                    "status": "PROVIDER_ERROR",
                                    "assessment": None,
                                    "proposed_category": None,
                                    "reason": (
                                        "Исчерпан лимит времени классификации; "
                                        "требуется ручная сверка."
                                    ),
                                    "conditions": [],
                                    "missing_context": [],
                                    "citations": [],
                                    "registry_citations": [],
                                },
                            )
                        )
                        continue
                    trade_name, dosage, diagnosis, code, item_id, fragments = payload
                    model_calls += 1
                    result = classify_purchase(
                        index.snapshot,
                        request_id=str(application_id),
                        item_id=str(item_id),
                        trade_name=trade_name,
                        dosage=dosage,
                        diagnosis=diagnosis,
                        diagnosis_code=code,
                        context={"Код МКБ-10": code},
                        complete_json=complete_json,
                        registry_snapshot=index.snapshot,
                        evidence_fragments=fragments,
                    )
                    prepared.append((check_id, None, _result_dict(result, **metadata)))
                    if result.status.value not in {"PROVIDER_ERROR", "INVALID_RESPONSE"}:
                        model_successes += 1
                    used_tokens = provider.last_total_tokens
                    if type(used_tokens) is int:
                        tokens = (tokens or 0) + used_tokens
        except (OSError, ValueError, ProviderError):
            for check_id, _, metadata in pending:
                prepared.append(
                    (
                        check_id,
                        None,
                        {
                            "status": "PROVIDER_ERROR",
                            "assessment": None,
                            "proposed_category": None,
                            "reason": "GigaChat недоступен или не настроен; нужна ручная сверка.",
                            "conditions": [],
                            "missing_context": [],
                            "citations": [],
                            "registry_citations": [],
                            **metadata,
                        },
                    )
                )
    elif pending:
        for check_id, _, metadata in pending:
            prepared.append(
                (
                    check_id,
                    None,
                    {
                        "status": "PROVIDER_ERROR",
                        "assessment": None,
                        "proposed_category": None,
                        "reason": "Не настроены учётные данные GigaChat.",
                        "conditions": [],
                        "missing_context": [],
                        "citations": [],
                        "registry_citations": [],
                        **metadata,
                    },
                )
            )

    outcomes = {check_id: outcome for check_id, _, outcome in prepared}
    dosage_by_check = {record[0]: record[3] for record in records}
    for check_id, outcome in outcomes.items():
        outcome["dosage_check"] = _dosage_check(dosage_by_check.get(check_id))
    # The GigaChat provider instance is already closed. Store counts/statuses,
    # never credentials or raw request payloads.
    with sessions.begin() as session:
        application = session.get(Application, application_id, with_for_update=True)
        if application is None or (application.demo_meta or {}).get("analysis_run_id") != run_id:
            return {"attempted": False, "reason": "ANALYSIS_RUN_CHANGED", "pairs": 0}
        checks = {
            row.id: row
            for row in session.scalars(
                select(ItemCheck)
                .join(ApplicationItem)
                .where(ApplicationItem.application_id == application_id)
            )
        }
        for check_id, outcome in outcomes.items():
            check = checks.get(check_id)
            if check is None:
                continue
            evidence = check.evidence or {}
            owns_category = bool(evidence.get("ml2_owns_category"))
            if check.category is None or owns_category:
                # A retry can withdraw a previous proposal. Official conflicts,
                # unmet rule conditions and ambiguous registrations take precedence.
                owns_category = (
                    outcome["status"] == "PROPOSED"
                    and bool(outcome["citations"])
                    and evidence.get("reason_code") in {
                        "OFFICIAL_COPY_MENTION", "OFFICIAL_DOCUMENT_MENTION",
                        "OFFICIAL_LINK_REQUIRES_REVIEW",
                        # The static index predates this diagnosis. ML-2 already
                        # checked the catalog edition, ICD scope and fresh text.
                        "OFFICIAL_DIAGNOSIS_NOT_INDEXED",
                    }
                )
                check.category = next(
                    (DrugCategory(key) for key, label in CATEGORIES.items()
                     if owns_category and label == outcome["proposed_category"]), None
                )
                if check.category is not None:
                    check.needs_review = True
            review_sources = list(evidence.get("review_sources") or [])
            seen = {(row.get("fragment_id"), row.get("quote")) for row in review_sources}
            for citation in outcome.get("citations", []):
                key = (citation.get("fragment_id"), citation.get("quote"))
                if key not in seen:
                    review_sources.append(citation)
                    seen.add(key)
            check.evidence = evidence | {
                "ml2_owns_category": owns_category,
                "ml2_classification": outcome,
                "review_sources": review_sources[:20],
            }
        statuses = [outcome.get("status") for outcome in outcomes.values()]
        application.demo_meta = (application.demo_meta or {}) | {
            "ml2_attempted": model_calls > 0,
            "ml2_pairs": len(outcomes),
            "ml2_status_counts": {
                status: statuses.count(status) for status in sorted(set(statuses))
            },
            "ml2_model_tokens": tokens,
            "ml2_checked_at": datetime.now(UTC).isoformat(),
        }
    return {
        "attempted": model_calls > 0,
        "model_calls": model_calls,
        "successful": model_successes,
        "tokens": tokens,
        "pairs": len(outcomes),
    }
