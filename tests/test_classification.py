from src.llm_rag.classification import (
    MedicineReference,
    build_analysis_item,
    match_original,
    match_trade_name,
)


def test_exact_match_ignores_case_and_extra_spaces():
    reference = [
        MedicineReference(
            trade_name="DemoMedicine",
            inn="DemoINN",
        )
    ]

    result = match_trade_name("  demomedicine  ", reference)

    assert result.status == "MATCHED"
    assert result.inn == "DemoINN"


def test_unknown_trade_name_is_not_guessed():
    reference = [
        MedicineReference(
            trade_name="DemoMedicine",
            inn="DemoINN",
        )
    ]

    result = match_trade_name("UnknownMedicine", reference)

    assert result.status == "NOT_FOUND"
    assert result.inn is None


def test_ambiguous_trade_name_is_not_matched_automatically():
    reference = [
        MedicineReference("DemoMedicine", "DemoINN-A"),
        MedicineReference("demomedicine", "DemoINN-B"),
    ]

    result = match_trade_name("DemoMedicine", reference)

    assert result.status == "AMBIGUOUS"
    assert result.inn is None
    assert set(result.candidates) == {"DemoINN-A", "DemoINN-B"}


def test_empty_name_is_invalid():
    result = match_trade_name("   ", [])

    assert result.status == "INVALID_INPUT"
    assert result.inn is None


def test_match_original_returns_matched_inn():
    original = {"trade_name": "DemoMedicine"}
    reference = [MedicineReference("DemoMedicine", "DemoINN")]

    result = match_original(original, reference)

    assert result["inn"] == "DemoINN"
    assert result["upstream_status"] == "PROCESSED"
    assert result["missing_data"] == []


def test_match_original_marks_unknown_name_as_partial():
    original = {"trade_name": "UnknownMedicine"}

    result = match_original(original, [])

    assert result["inn"] is None
    assert result["upstream_status"] == "PARTIAL"
    assert result["missing_data"] == ["inn"]


def test_match_original_rejects_invalid_trade_name():
    original = {"trade_name": None}

    result = match_original(original, [])

    assert result["inn"] is None
    assert result["upstream_status"] == "FAILED"
    assert result["missing_data"] == ["trade_name"]


def test_build_analysis_item_creates_valid_demo_item():
    original = {
        "trade_name": "DemoMedicine",
        "dosage": "10 mg",
        "quantity": 1,
        "amount": "100.00",
        "currency": "EUR",
    }
    reference = [
        MedicineReference("DemoMedicine", "DemoINN")
    ]

    item = build_analysis_item("test-1", original, reference)

    assert item.inn == "DemoINN"
    assert item.upstream_status == "PROCESSED"
    assert item.category is None
    assert item.dosage_check.status == "NOT_CHECKED"
    assert item.missing_data == []

import json
from pathlib import Path

from llm_rag.classification import MedicineReference, build_analysis_item
from llm_rag.corpus import Corpus


def _load_demo_corpus() -> Corpus:
    path = Path(__file__).parent.parent / "examples" / "corpus.json"
    return Corpus.model_validate(json.loads(path.read_text(encoding="utf-8")))


def _original(name: str) -> dict:
    return {
        "trade_name": name,
        "dosage": "демо-строка",
        "quantity": 1,
        "amount": "100.00",
        "currency": "RUB",
    }


REFERENCE = [
    MedicineReference("Демо-А", "демо-вещество-а"),
    MedicineReference("Нет основания", "демо-вне-корпуса"),
]


def test_confirmed_position_takes_category_and_reason_from_corpus():
    item = build_analysis_item(
        "row-1", _original("Демо-А"), REFERENCE,
        diagnosis="Демо-диагноз", corpus=_load_demo_corpus(),
    )
    assert item.inn == "демо-вещество-а"
    assert item.category == "А"
    assert item.classification_reason == "Основная терапия в тестовом перечне."
    assert item.source_refs == ["master-demo"]
    assert item.upstream_status == "PROCESSED"


def test_unknown_drug_has_no_category():
    item = build_analysis_item(
        "row-2", _original("Неизвестный демо-препарат"), REFERENCE,
        diagnosis="Демо-диагноз", corpus=_load_demo_corpus(),
    )
    assert item.inn is None
    assert item.category is None
    assert item.upstream_status == "PARTIAL"
    assert "inn" in item.missing_data


def test_no_rule_in_corpus_gives_warning_instead_of_guess():
    item = build_analysis_item(
        "row-3", _original("Нет основания"), REFERENCE,
        diagnosis="Демо-диагноз", corpus=_load_demo_corpus(),
    )
    assert item.inn == "демо-вне-корпуса"
    assert item.category is None
    assert item.classification_reason is None
    assert item.upstream_status == "PARTIAL"
    assert "category" in item.missing_data