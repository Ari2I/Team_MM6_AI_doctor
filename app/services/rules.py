"""Deterministic pair checks on in-memory reference data."""

from __future__ import annotations

from collections.abc import Iterable

from app.config import MatchingSettings, get_matching_settings
from app.models.enums import ReviewReason
from app.schemas.analysis import DiagnosisMatch, DrugMatch, PairCheck, ReferenceSnapshot
from app.services.text_utils import normalize_text

_REVIEW = (
    "\u0422\u0440\u0435\u0431\u0443\u0435\u0442\u0441\u044f \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0430 "  # noqa: E501
    "\u0447\u0435\u043b\u043e\u0432\u0435\u043a\u043e\u043c"
)
_COMMENTS = {
    ReviewReason.DRUG_NOT_FOUND: "\u041f\u0440\u0435\u043f\u0430\u0440\u0430\u0442 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d. "  # noqa: E501
    + _REVIEW,
    ReviewReason.DRUG_AMBIGUOUS: (
        "\u041f\u0440\u0435\u043f\u0430\u0440\u0430\u0442 \u0441\u043e\u043f\u043e\u0441\u0442\u0430\u0432\u043b\u0435\u043d "  # noqa: E501
        "\u043d\u0435\u043e\u0434\u043d\u043e\u0437\u043d\u0430\u0447\u043d\u043e. "
    )
    + _REVIEW,
    ReviewReason.DIAGNOSIS_NOT_FOUND: "\u0414\u0438\u0430\u0433\u043d\u043e\u0437 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d. "  # noqa: E501
    + _REVIEW,
    ReviewReason.NO_RULE_FOR_PAIR: (
        "\u0414\u043b\u044f \u043f\u0430\u0440\u044b \u043f\u0440\u0435\u043f\u0430\u0440\u0430\u0442-"  # noqa: E501
        "\u0434\u0438\u0430\u0433\u043d\u043e\u0437 \u043d\u0435\u0442 \u043f\u0440\u0430\u0432\u0438\u043b\u0430. "  # noqa: E501
    )
    + _REVIEW,
    ReviewReason.LOW_CONFIDENCE: "\u0423\u0432\u0435\u0440\u0435\u043d\u043d\u043e\u0441\u0442\u044c \u043d\u0438\u0436\u0435 \u043f\u043e\u0440\u043e\u0433\u0430. "  # noqa: E501
    + _REVIEW,
    ReviewReason.CONFLICTING_CATEGORIES: (
        "\u041f\u043e\u043b\u0443\u0447\u0435\u043d\u044b \u0440\u0430\u0437\u043d\u044b\u0435 "
        "\u043a\u0430\u0442\u0435\u0433\u043e\u0440\u0438\u0438. "
    )
    + _REVIEW,
}


def pairs_for_item(item_diagnosis: str | None, application_diagnoses: Iterable[str]) -> list[str]:
    """Return the diagnoses that must be checked for one application item."""

    if item_diagnosis is not None and normalize_text(item_diagnosis):
        return [item_diagnosis]
    return [diagnosis for diagnosis in application_diagnoses if normalize_text(diagnosis)]


def _review(reason: ReviewReason, snapshot: ReferenceSnapshot) -> PairCheck:
    return PairCheck(
        reference_version=snapshot.reference_version,
        needs_review=True,
        review_reason=reason,
        comment=_COMMENTS[reason],
    )


def check_pair(
    drug_match: DrugMatch,
    diagnosis_match: DiagnosisMatch,
    snapshot: ReferenceSnapshot,
    settings: MatchingSettings | None = None,
) -> PairCheck:
    """Check one matched drug/diagnosis pair against reference rules."""

    if drug_match.status == "not_found":
        return _review(ReviewReason.DRUG_NOT_FOUND, snapshot)
    if drug_match.status == "ambiguous":
        return _review(ReviewReason.DRUG_AMBIGUOUS, snapshot)
    if diagnosis_match.status == "not_found":
        return _review(ReviewReason.DIAGNOSIS_NOT_FOUND, snapshot)
    if diagnosis_match.status == "ambiguous":
        return _review(ReviewReason.LOW_CONFIDENCE, snapshot)

    matching_settings = settings or get_matching_settings()
    if (
        drug_match.confidence * 100 < matching_settings.drug_auto_accept_threshold
        or diagnosis_match.confidence * 100 < matching_settings.diagnosis_auto_accept_threshold
    ):
        return _review(ReviewReason.LOW_CONFIDENCE, snapshot)

    drug_mnn = normalize_text(drug_match.mnn)
    diagnosis_id = normalize_text(diagnosis_match.diagnosis_id)
    rule = next(
        (
            candidate
            for candidate in snapshot.rules
            if candidate.normalized_mnn == drug_mnn
            and candidate.normalized_diagnosis_id == diagnosis_id
            and candidate.category is not None
        ),
        None,
    )
    if rule is None:
        return _review(ReviewReason.NO_RULE_FOR_PAIR, snapshot)
    return PairCheck(
        category=rule.category,
        source=rule.source,
        reference_version=snapshot.reference_version,
        needs_review=False,
    )


def detect_conflicts(checks_for_item: list[PairCheck]) -> list[PairCheck]:
    """Mark every check for an item when its diagnoses disagree on category."""

    categories = {check.category for check in checks_for_item if check.category is not None}
    if len(categories) <= 1:
        return checks_for_item
    for check in checks_for_item:
        check.needs_review = True
        check.review_reason = ReviewReason.CONFLICTING_CATEGORIES
        check.comment = _COMMENTS[ReviewReason.CONFLICTING_CATEGORIES]
    return checks_for_item
