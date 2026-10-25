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