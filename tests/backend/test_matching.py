from app.schemas.analysis import ReferenceDiagnosis, ReferenceDrug, ReferenceSnapshot
from app.services.matching import match_diagnosis, match_drug


def snapshot() -> ReferenceSnapshot:
    return ReferenceSnapshot(
        reference_version="test-1",
        drugs=[
            ReferenceDrug(trade_name="Парацетамол", mnn="Paracetamol"),
            ReferenceDrug(trade_name="Ибупрофен", mnn="Ibuprofen"),
        ],
        diagnoses=[
            ReferenceDiagnosis(diagnosis_id="d1", name="Жёлтая лихорадка"),
            ReferenceDiagnosis(diagnosis_id="d2", name="Грипп"),
        ],
    )


def test_exact_match_is_case_and_yo_insensitive() -> None:
    result = match_drug("ПАРАЦЕТАМОЛ", snapshot())
    assert result.status == "matched"
    assert result.mnn == "Paracetamol"
    assert match_diagnosis("желтая лихорадка", snapshot()).diagnosis_id == "d1"


def test_typo_is_fuzzy_matched_or_ambiguous() -> None:
    result = match_drug("Парацетомол", snapshot())
    assert result.status in {"matched", "ambiguous"}
    assert result.confidence >= 0.75


def test_unknown_drug_and_diagnosis() -> None:
    assert match_drug("Несуществующий препарат", snapshot()).status == "not_found"
    assert match_diagnosis("Несуществующий диагноз", snapshot()).status == "not_found"
