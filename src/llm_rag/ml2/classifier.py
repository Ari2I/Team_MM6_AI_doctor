"""Изолированный прототип AI/ML №2 для оценки пары МНН + диагноз.

Модуль не выполняет сетевых запросов и не меняет общий конвейер приложения.
Вызывающий код передаёт ему только нужные фрагменты официальных источников и
адаптер GigaChat. Категория определяется по рабочим правилам и цитатам КР.
Решение о компенсации принимает человек.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum

from .policy import load_classification_rules, load_system_prompt
from .registry import RegistryIndex, RegistrySnapshot, inn_key

_RULES = load_classification_rules()


class Assessment(StrEnum):
    CORE_TREATMENT = "CORE_TREATMENT"
    SUPPORTIVE_TREATMENT = "SUPPORTIVE_TREATMENT"
    EXPLICITLY_NOT_RECOMMENDED = "EXPLICITLY_NOT_RECOMMENDED"
    OPTIONAL_PRODUCT = "OPTIONAL_PRODUCT"
    MENTIONED_ONLY = "MENTIONED_ONLY"
    NOT_FOUND = "NOT_FOUND"
    INSUFFICIENT_CONTEXT = "INSUFFICIENT_CONTEXT"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"


class ClassificationStatus(StrEnum):
    PROPOSED = "PROPOSED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    NEEDS_CONTEXT = "NEEDS_CONTEXT"
    NO_EVIDENCE = "NO_EVIDENCE"
    INN_UNRESOLVED = "INN_UNRESOLVED"
    MULTI_INGREDIENT_REVIEW = "MULTI_INGREDIENT_REVIEW"
    CONFLICT = "CONFLICT"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    SOURCE_ERROR = "SOURCE_ERROR"


@dataclass(frozen=True)
class EvidenceFragment:
    """Один сохранённый фрагмент рекомендации с проверяемым происхождением."""

    fragment_id: str
    source_id: str
    title: str
    version: str
    url: str
    location: str
    text: str
    copy_url: str | None = None


@dataclass(frozen=True)
class ClassificationRequest:
    """Входная пара после сопоставления торгового названия с МНН."""

    request_id: str
    item_id: str
    diagnosis: str
    inn: str
    fragments: tuple[EvidenceFragment, ...]
    context: Mapping[str, str]


@dataclass(frozen=True)
class Citation:
    fragment_id: str
    source_id: str
    title: str
    version: str
    url: str
    location: str
    quote: str
    copy_url: str | None = None


@dataclass(frozen=True)
class ClassificationResult:
    request_id: str
    item_id: str
    diagnosis: str
    inn: str | None
    assessment: Assessment | None
    proposed_category: str | None
    status: ClassificationStatus
    conditions: tuple[str, ...] = ()
    missing_context: tuple[str, ...] = ()
    citations: tuple[Citation, ...] = ()
    registry_citations: tuple[Citation, ...] = ()
    candidate_mnns: tuple[str, ...] = ()
    review_reason: str | None = None
    policy_version: str = _RULES.policy_version
    model_assessment: Assessment | None = None


class InvalidModelResponse(ValueError):
    """Ответ модели не соответствует контракту или не подтверждён цитатой."""


# Семантика категорий и лексические проверки находятся в файле правил.
if set(_RULES.assessments) != {value.value for value in Assessment}:
    raise ValueError("Перечень тезисов в правилах не совпадает с контрактом модели.")


def _role_supported(inn: str, quote: str, rule) -> bool:
    """Применить декларативные проверки цитаты; дословность проверяется отдельно."""
    ingredient = re.escape(inn_key(inn)).replace(r"\ ", r"\s+")
    clauses = re.split(r"[.;!?\n]", quote) if rule.same_clause_inn else [quote]
    for clause in clauses:
        clause = clause.casefold().replace("ё", "е")
        if rule.same_clause_inn and not re.search(
            r"(?<!\w)" + ingredient + r"[а-я]{0,3}(?!\w)", clause
        ):
            continue
        if any(re.search(pattern, clause, re.IGNORECASE) for pattern in rule.reject_patterns):
            continue
        if any(re.search(pattern, clause, re.IGNORECASE) for pattern in rule.quote_patterns):
            return True
    return False


def _quote_options(text: str) -> list[str]:
    options = [
        part.strip()
        for part in re.split(r"(?<=[.!?])\s+", text)
        if part.strip() and len(part.strip()) <= 3000
    ]
    if not options and text:
        options = [text[:3000].strip()]
    return list(dict.fromkeys(options[:20]))


SYSTEM_PROMPT = load_system_prompt()


def prompt_for(request: ClassificationRequest) -> tuple[str, str]:
    """Собрать фиксированную инструкцию и JSON-контекст для модельного адаптера."""

    if not request.diagnosis.strip() or not request.inn.strip():
        raise ValueError("Для оценки нужны диагноз и МНН.")
    if len(request.fragments) > 20:
        raise ValueError("Передавайте не более 20 заранее отобранных фрагментов.")
    if len({fragment.fragment_id for fragment in request.fragments}) != len(
        request.fragments
    ):
        raise ValueError("fragment_id должен быть уникальным в одном запросе.")
    if any(not fragment.text.strip() for fragment in request.fragments):
        raise ValueError("Пустые фрагменты нельзя отправлять модели.")

    payload = {
        "diagnosis": request.diagnosis,
        "inn": request.inn,
        "clinical_context": dict(request.context),
        "fragments": [
            {
                "fragment_id": fragment.fragment_id,
                "source_id": fragment.source_id,
                "title": fragment.title,
                "version": fragment.version,
                "location": fragment.location,
                "text": fragment.text,
                "quote_options": _quote_options(fragment.text),
            }
            for fragment in request.fragments
        ],
    }
    return SYSTEM_PROMPT, json.dumps(payload, ensure_ascii=False)


def gigachat_completer(provider) -> Callable[[str, str], Mapping[str, object]]:
    """Адаптировать официальный GigaChatProvider к строгому JSON-контракту ML-2."""

    def complete(system: str, user_json: str) -> Mapping[str, object]:
        payload = json.loads(user_json)
        fragments = payload["fragments"]
        if not fragments:
            raise ValueError("GigaChat нельзя вызывать без фрагментов источника.")
        evidence_options = []
        for fragment in fragments:
            quotes = fragment.get("quote_options") or _quote_options(fragment["text"])
            if quotes:
                evidence_options.append(
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "fragment_id": {
                                "type": "string",
                                "enum": [fragment["fragment_id"]],
                            },
                            "quote": {
                                "type": "string",
                                "enum": list(dict.fromkeys(quotes)),
                            },
                        },
                        "required": ["fragment_id", "quote"],
                    }
                )
        if not evidence_options:
            raise ValueError(
                "Из фрагментов не удалось подготовить точные варианты цитат."
            )
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "assessment": {
                    "type": "string",
                    "enum": [value.value for value in Assessment],
                },
                "conditions": {
                    "type": "array",
                    "maxItems": 20,
                    "items": {"type": "string", "maxLength": 500},
                },
                "missing_context": {
                    "type": "array",
                    "maxItems": 20,
                    "items": {"type": "string", "maxLength": 200},
                },
                "evidence": {
                    "type": "array",
                    "maxItems": 20,
                    "items": (
                        evidence_options[0]
                        if len(evidence_options) == 1
                        else {"oneOf": evidence_options}
                    ),
                },
            },
            "required": ["assessment", "conditions", "missing_context", "evidence"],
        }
        return provider.complete_json(payload, system, schema)

    return complete


def _parse_response(response: str | Mapping[str, object]) -> dict:
    if isinstance(response, str):
        try:
            value = json.loads(response)
        except (json.JSONDecodeError, UnicodeError):
            raise InvalidModelResponse("Модель вернула некорректный JSON.") from None
    elif isinstance(response, Mapping):
        value = dict(response)
    else:
        raise InvalidModelResponse("Ответ модели должен быть JSON-объектом.")
    expected = {"assessment", "conditions", "missing_context", "evidence"}
    if not isinstance(value, dict) or set(value) != expected:
        raise InvalidModelResponse("Набор полей ответа не совпадает со схемой.")
    return value


def validate_response(
    request: ClassificationRequest,
    response: str | Mapping[str, object],
) -> ClassificationResult:
    """Проверить структуру и дословность ссылок, затем определить категорию по правилам."""

    value = _parse_response(response)
    try:
        assessment = Assessment(value["assessment"])
    except (ValueError, TypeError):
        raise InvalidModelResponse("Неизвестный тип клинического тезиса.") from None

    conditions = value["conditions"]
    missing = value["missing_context"]
    raw_evidence = value["evidence"]
    if (
        not isinstance(conditions, list)
        or any(not isinstance(x, str) for x in conditions)
        or not isinstance(missing, list)
        or any(not isinstance(x, str) for x in missing)
        or not isinstance(raw_evidence, list)
    ):
        raise InvalidModelResponse("Некорректные типы полей ответа.")

    fragments = {fragment.fragment_id: fragment for fragment in request.fragments}
    citations: list[Citation] = []
    for row in raw_evidence:
        if not isinstance(row, dict) or set(row) != {"fragment_id", "quote"}:
            raise InvalidModelResponse("Некорректная ссылка на основание.")
        fragment_id, quote = row["fragment_id"], row["quote"]
        if (
            not isinstance(fragment_id, str)
            or not isinstance(quote, str)
            or not quote.strip()
        ):
            raise InvalidModelResponse("Пустая ссылка или цитата.")
        fragment = fragments.get(fragment_id)
        if fragment is None or quote not in fragment.text:
            raise InvalidModelResponse("Цитата не найдена в переданном источнике.")
        citations.append(
            Citation(
                fragment_id=fragment.fragment_id,
                source_id=fragment.source_id,
                title=fragment.title,
                version=fragment.version,
                url=fragment.url,
                location=fragment.location,
                quote=quote,
                copy_url=fragment.copy_url,
            )
        )

    if assessment == Assessment.NOT_FOUND and citations:
        raise InvalidModelResponse("Для NOT_FOUND модель не должна возвращать цитаты.")
    if assessment != Assessment.NOT_FOUND and not citations:
        raise InvalidModelResponse("Для оценки нужна точная цитата из источника.")
    if (
        assessment == Assessment.CONFLICTING_EVIDENCE
        and len({c.fragment_id for c in citations}) < 2
    ):
        raise InvalidModelResponse(
            "Для конфликта нужны минимум две подтверждённые цитаты."
        )
    if assessment == Assessment.INSUFFICIENT_CONTEXT and not missing:
        raise InvalidModelResponse(
            "Для недостатка контекста укажите недостающее условие."
        )

    model_assessment = assessment
    rule = _RULES.assessments[assessment.value]
    assessment_note = None
    if rule.category and missing:
        assessment = Assessment.INSUFFICIENT_CONTEXT
    elif rule.category and not any(
        _role_supported(request.inn, citation.quote, rule) for citation in citations
    ):
        assessment = Assessment.MENTIONED_ONLY
        assessment_note = "Цитаты не прошли проверку роли по рабочим правилам классификации."

    category = _RULES.assessments[assessment.value].category
    if assessment == Assessment.NOT_FOUND:
        status = ClassificationStatus.NO_EVIDENCE
        reason = "В переданных фрагментах основание не найдено. Это не категория В."
    elif assessment == Assessment.INSUFFICIENT_CONTEXT:
        status = ClassificationStatus.NEEDS_CONTEXT
        reason = "Не хватает клинического контекста для определения применимости."
    elif assessment == Assessment.CONFLICTING_EVIDENCE:
        status = ClassificationStatus.CONFLICT
        reason = "В источниках найдены несовместимые основания."
    elif category:
        status = ClassificationStatus.PROPOSED
        reason = _RULES.assessments[assessment.value].reason
    else:
        status = ClassificationStatus.NEEDS_REVIEW
        reason = assessment_note or "Цитаты не устанавливают категорию; требуется ручная сверка."

    if category is None:
        citations = [] if assessment == Assessment.NOT_FOUND else citations
    return ClassificationResult(
        request_id=request.request_id,
        item_id=request.item_id,
        diagnosis=request.diagnosis,
        inn=request.inn,
        assessment=assessment,
        proposed_category=category,
        status=status,
        conditions=tuple(conditions),
        missing_context=tuple(missing),
        citations=tuple(citations),
        review_reason=reason,
        policy_version=_RULES.policy_version,
        model_assessment=model_assessment,
    )


def classify_pair(
    request: ClassificationRequest,
    complete_json: Callable[[str, str], str | Mapping[str, object]],
) -> ClassificationResult:
    """Вызвать переданный адаптер GigaChat и безопасно проверить его ответ.

    Адаптер получает только фиксированную системную инструкцию и JSON с диагнозом,
    МНН, контекстом и публичными фрагментами. Здесь намеренно нет самостоятельного
    HTTP-клиента и доступа к секретам: интеграция с действующим клиентом API требует
    отдельного согласования владельцами общего кода.
    """

    if not request.fragments:
        return ClassificationResult(
            request_id=request.request_id,
            item_id=request.item_id,
            diagnosis=request.diagnosis,
            inn=request.inn,
            assessment=Assessment.NOT_FOUND,
            proposed_category=None,
            status=ClassificationStatus.NO_EVIDENCE,
            review_reason="Для пары не переданы фрагменты клинической рекомендации.",
        )
    system, user = prompt_for(request)
    try:
        response = complete_json(system, user)
    except Exception:  # noqa: BLE001 — граница внешнего адаптера, текст ошибки не раскрывается.
        return ClassificationResult(
            request_id=request.request_id,
            item_id=request.item_id,
            diagnosis=request.diagnosis,
            inn=request.inn,
            assessment=None,
            proposed_category=None,
            status=ClassificationStatus.PROVIDER_ERROR,
            review_reason="GigaChat недоступен или вернул ошибку; нужна ручная проверка.",
        )
    try:
        return validate_response(request, response)
    except InvalidModelResponse as error:
        return ClassificationResult(
            request_id=request.request_id,
            item_id=request.item_id,
            diagnosis=request.diagnosis,
            inn=request.inn,
            assessment=None,
            proposed_category=None,
            status=ClassificationStatus.INVALID_RESPONSE,
            review_reason=str(error),
        )


def classify_purchase(
    snapshot: RegistrySnapshot,
    *,
    request_id: str,
    item_id: str,
    trade_name: str,
    dosage: str | None,
    diagnosis: str,
    context: Mapping[str, str] | None,
    complete_json: Callable[[str, str], str | Mapping[str, object]],
    evidence_fragments: tuple[EvidenceFragment, ...] = (),
) -> ClassificationResult:
    """Сопоставить МНН по локальному ГРЛС и оценить переданные фрагменты КР."""
    drug = RegistryIndex(snapshot).resolve(trade_name, dosage)
    registry_citations = tuple(
        Citation(
            fragment_id=row.source.citation()["fragment_id"],
            source_id=row.source.citation()["source_id"],
            title=row.source.title,
            version=row.source.version,
            url=row.source.url,
            location=row.source.location,
            quote=row.source.quote,
        )
        for row in drug.registrations[:8]
    )
    if drug.mnn is None:
        return ClassificationResult(
            request_id=request_id,
            item_id=item_id,
            diagnosis=diagnosis,
            inn=None,
            assessment=None,
            proposed_category=None,
            status=ClassificationStatus.INN_UNRESOLVED,
            registry_citations=registry_citations,
            candidate_mnns=tuple(drug.candidates),
            review_reason=(
                "МНН сопоставлено неоднозначно по текущему снимку ГРЛС."
                if drug.candidates
                else "Для торгового названия не найдено МНН в текущем снимке ГРЛС."
            ),
        )
    if "+" in drug.mnn:
        return ClassificationResult(
            request_id=request_id,
            item_id=item_id,
            diagnosis=diagnosis,
            inn=drug.mnn,
            assessment=None,
            proposed_category=None,
            status=ClassificationStatus.MULTI_INGREDIENT_REVIEW,
            registry_citations=registry_citations,
            review_reason=(
                "В составе несколько МНН. Сначала разберите компоненты и "
                "классифицируйте каждый отдельно."
            ),
        )

    request = ClassificationRequest(
        request_id=request_id,
        item_id=item_id,
        diagnosis=diagnosis,
        inn=drug.mnn,
        fragments=evidence_fragments,
        context=context or {},
    )
    result = classify_pair(request, complete_json)
    return replace(result, registry_citations=registry_citations)
