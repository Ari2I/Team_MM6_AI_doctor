from app.models.enums import DrugCategory, ReviewReason
from app.schemas.analysis import (
    DiagnosisMatch,
    DrugMatch,
    PairCheck,
    ReferenceDiagnosis,
    ReferenceDrug,
    ReferenceRule,
    ReferenceSnapshot,
)
from app.services.rules import check_pair, detect_conflicts, pairs_for_item


def snapshot() -> ReferenceSnapshot:
    return ReferenceSnapshot(
        reference_version="test-1",
        drugs=[ReferenceDrug(trade_name="Препарат", mnn="drug-mnn")],
        diagnoses=[
            ReferenceDiagnosis(diagnosis_id="d1", name="Диагноз 1"),
            ReferenceDiagnosis(diagnosis_id="d2", name="Диагноз 2"),
        ],
        rules=[
            ReferenceRule(
                mnn="drug-mnn", diagnosis_id="d1", category=DrugCategory.A, source="rules"
            ),
            ReferenceRule(
                mnn="drug-mnn", diagnosis_id="d2", category=DrugCategory.B, source="rules"
            ),
        ],
    )


def matched_pair(diagnosis_id: str) -> tuple[DrugMatch, DiagnosisMatch]:
    return (
        DrugMatch(mnn="drug-mnn", confidence=1, status="matched"),
        DiagnosisMatch(diagnosis_id=diagnosis_id, confidence=1, status="matched"),
    )


def test_pairs_for_item_uses_own_or_all_application_diagnoses() -> None:
    assert pairs_for_item("Диагноз 1", ["Диагноз 1", "Диагноз 2"]) == ["Диагноз 1"]
    assert pairs_for_item(None, ["Диагноз 1", "Диагноз 2"]) == ["Диагноз 1", "Диагноз 2"]


def test_missing_rule_requires_review() -> None:
    drug, diagnosis = matched_pair("unknown")
    result = check_pair(drug, diagnosis, snapshot())
    assert result.needs_review is True
    assert result.review_reason is ReviewReason.NO_RULE_FOR_PAIR


def test_conflicting_categories_are_marked() -> None:
    checks = [
        PairCheck(category=DrugCategory.A, reference_version="test-1"),
        PairCheck(category=DrugCategory.B, reference_version="test-1"),
    ]
    detect_conflicts(checks)
    assert all(check.needs_review for check in checks)
    assert all(check.review_reason is ReviewReason.CONFLICTING_CATEGORIES for check in checks)
