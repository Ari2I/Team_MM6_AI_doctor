"""Проверка применимости и полноты оснований по проверенной разметке демонстрационного корпуса."""

from dataclasses import dataclass

from llm_rag.contracts import AnalysisItem, AnalysisRequest, ExplanationStatus
from llm_rag.corpus import Assertion, Fragment, normalize


@dataclass(frozen=True)
class EvidenceClaim:
    fragment: Fragment
    assertion: Assertion


@dataclass(frozen=True)
class EvidencePacket:
    status: ExplanationStatus
    claims: tuple[EvidenceClaim, ...]
    review_evidence: tuple[EvidenceClaim, ...]
    reasons: tuple[str, ...]
    missing_data: tuple[str, ...]
    required_kinds: frozenset[str]


def build_evidence(
    request: AnalysisRequest, item: AnalysisItem, fragments: list[Fragment]
) -> EvidencePacket:
    reasons: list[str] = []
    missing = list(item.missing_data) + list(item.dosage_check.missing_data)
    required = {"CATEGORY"}
    if item.dosage_check.status in {"VALID", "INVALID"}:
        required.add("DOSAGE")
        if item.dosage_check.reason is None:
            missing.append("dosage_check.reason")
    if item.dosage_check.status == "INSUFFICIENT_DATA":
        reasons.append("UPSTREAM_DOSAGE_INSUFFICIENT")
        if not item.dosage_check.missing_data:
            missing.append("dosage_check.missing_data")
    if item.upstream_status != "PROCESSED":
        reasons.append("UPSTREAM_INCOMPLETE")
        missing.append("upstream_result")
    for field in ("inn", "category", "classification_reason"):
        if getattr(item, field) is None:
            missing.append(field)
    supported: list[EvidenceClaim] = []
    reviewed: list[EvidenceClaim] = []
    conflict = False
    for fragment in fragments:
        # Повторная проверка не позволяет обойти фильтр применимости прямым вызовом функции.
        if item.inn is None or normalize(fragment.inn) != normalize(item.inn):
            continue
        if normalize(fragment.diagnosis) != normalize(request.diagnosis):
            continue
        relevant = [a for a in fragment.assertions if a.kind == "CATEGORY" or (
            "DOSAGE" in required and a.dosage == item.original.dosage
        )]
        if not relevant:
            continue
        reviewed.extend(EvidenceClaim(fragment, assertion) for assertion in relevant)
        # Условия важны для подтверждающего основания или возможного противоречия.
        # Посторонние обоснования в дополнительных записях не должны создавать новые требования к данным.
        relevant = [assertion for assertion in relevant if (
            assertion.kind == "CATEGORY" and (
                assertion.category != item.category or (
                    item.classification_reason is not None
                    and normalize(assertion.upstream_reason) == normalize(item.classification_reason)
                )
            )
        ) or (
            assertion.kind == "DOSAGE" and (
                assertion.dosage_status != item.dosage_check.status or (
                    item.dosage_check.reason is not None
                    and normalize(assertion.upstream_reason) == normalize(item.dosage_check.reason)
                )
            )
        )]
        if not relevant:
            continue
        # Явно невыполненное условие исключает фрагмент, даже если другое условие неизвестно.
        if any(key in request.context and request.context[key] != value
               for key, value in fragment.required_context.items()):
            reasons.append("SOURCE_CONDITION_NOT_MET")
            continue
        absent = [f"context.{key}" for key in fragment.required_context if key not in request.context]
        if absent:
            missing.extend(absent)
            reasons.append("SOURCE_CONDITION_MISSING")
            continue
        for assertion in relevant:
            if assertion.kind == "CATEGORY":
                if item.category is None:
                    continue
                if assertion.category != item.category:
                    conflict = True
                    reasons.append("CATEGORY_CONTRADICTED_BY_SOURCE")
                    continue
                reason = item.classification_reason
            else:
                if assertion.dosage_status != item.dosage_check.status:
                    conflict = True
                    reasons.append("DOSAGE_CONTRADICTED_BY_SOURCE")
                    continue
                reason = item.dosage_check.reason
            if reason is not None and normalize(assertion.upstream_reason) == normalize(reason):
                supported.append(EvidenceClaim(fragment, assertion))
    covered = {c.assertion.kind for c in supported}
    for kind in sorted(required - covered):
        reasons.append(f"NO_CONFIRMED_{kind}_REASON")
    if conflict:
        status = "SOURCE_CONFLICT"
        supported = []
    elif missing or "UPSTREAM_DOSAGE_INSUFFICIENT" in reasons:
        status = "INSUFFICIENT_DATA"
    elif not required.issubset(covered):
        status = "UNCONFIRMED"
    else:
        status = "SUPPORTED"
    return EvidencePacket(
        status=status, claims=tuple(supported), review_evidence=tuple(reviewed),
        reasons=tuple(dict.fromkeys(reasons)), missing_data=tuple(dict.fromkeys(missing)),
        required_kinds=frozenset(required),
    )
