"""Только синтетические результаты и публичные модели; фасад/SDK не вызываются."""

from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from app.models import Application
from app.services.integration_mapping import (
    prepare_integration_request,
    validate_integration_request,
)


def integration_result(application: Application, mode: str = "complete") -> dict[str, Any]:
    from llm_rag.integration_contracts import IntegrationResult

    request = validate_integration_request(prepare_integration_request(application, currency="RUB"))
    pairs = []
    pending = []
    for index, link in enumerate(request["links"]):
        identity = dict(request_id=request["request_id"], **link)
        if mode == "partial" and index == 0:
            pending.append(dict(**identity, reason="BUDGET_EXHAUSTED"))
            continue
        fallback = mode == "fallback"
        pairs.append(
            dict(
                **identity,
                decision=dict(
                    proposed_category=None if fallback else "Б",
                    classification_basis="NONE" if fallback else "ML2_PROPOSAL",
                ),
                policy_version="synthetic-policy-1",
                evidence_status="PROCESSING_ERROR" if fallback else "UNCONFIRMED",
                reasons=["SOURCE_ERROR"] if fallback else ["NEEDS_REVIEW"],
                generation_mode="FALLBACK" if fallback else "OFFLINE",
                form_status="MISMATCH" if index == 0 else "UNKNOWN",
                form_reason="Синтетическое предупреждение, не медицинская рекомендация.",
            )
        )
    snapshot = None
    manifest = dict(policy=dict(version="synthetic-policy-1", sha256="1" * 64))
    if mode == "evidence":
        from llm_rag.evidence_snapshot import EvidenceDocument, EvidenceSnapshot

        pair = pairs[0]
        document = EvidenceDocument(
            source_id="synthetic-source",
            fragment_id="synthetic-fragment",
            title="Синтетический источник",
            version="synthetic-1",
            url="https://example.org/synthetic-source",
            location="Тестовый раздел",
            text="Синтетическая цитата, не медицинская рекомендация.",
            diagnosis=next(
                row.name for row in application.diagnoses if str(row.id) == pair["diagnosis_id"]
            ),
            inn="Синтетическое вещество",
            retrieved_at=datetime.now(UTC).isoformat(),
        )
        snapshot = EvidenceSnapshot(snapshot_version="synthetic-evidence-1", documents=[document])
        manifest["clinical"] = dict(
            version=snapshot.snapshot_version,
            sha256=sha256(snapshot.model_dump_json().encode()).hexdigest(),
        )
        pair["inn"] = document.inn
        pair["evidence_status"] = "VERIFIED"
        pair["citations"] = [
            dict(
                source_id=document.source_id,
                fragment_id=document.fragment_id,
                title=document.title,
                version=document.version,
                url=document.url,
                copy_url=None,
                location=document.location,
                quote=document.text,
            )
        ]
    return IntegrationResult.model_validate(
        dict(
            request=request,
            completeness="PARTIAL" if pending else "COMPLETE",
            pairs=pairs,
            unprocessed_pairs=pending,
            manifest=manifest,
            evidence_snapshot=snapshot,
        )
    ).model_dump(mode="json")


def human_report() -> dict[str, Any]:
    """Один сохранённый результат чтения без необходимости импортировать AI."""
    return dict(
        schema_version="human-report.1",
        title="Синтетический отчёт",
        notice="Решение принимает человек.",
        summary="Синтетическая сводка.",
        recommendation_note="Требуется сверка.",
        review_note="Рекомендательный результат.",
        items=[
            dict(
                number=1,
                name="Синтетическая позиция",
                diagnosis="Синтетический диагноз",
                category="не установлена",
                evidence="проверка не выполнена",
                comment="Нужна сверка.",
                established=[],
                uncertain=[],
                questions=[],
                actions=["Сверьте данные."],
                sources=[],
            )
        ],
        costs=[],
        purchases_without_amount_count=1,
    )
