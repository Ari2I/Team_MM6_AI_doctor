"""Целостность независимых входов до реализации многодиагнозного импортёра."""

from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path

from openpyxl import load_workbook


ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "excel_cases" / "multidiagnosis_20261117"


def test_multidiagnosis_fixture_acceptance_matrix():
    manifest = json.loads((CASES / "manifest.json").read_text(encoding="utf-8"))
    hashes = json.loads((CASES / "SHA256.json").read_text(encoding="utf-8"))
    assert manifest["medical_validation"] == "PENDING_EXTERNAL_EXPERT"
    cases = {case["id"]: case for case in manifest["cases"]}
    assert set(cases) == {
        "single",
        "two",
        "ten",
        "fifty",
        "unknown",
        "missing_condition",
        "form_mismatch",
        "currencies",
        "invalid_link",
        "duplicate_id",
    }
    assert {cases[key]["diagnosis_count"] for key in ("single", "two", "ten", "fifty")} == {1, 2, 10, 50}
    assert {p.name for p in (CASES / "input").glob("*.xlsx")} == set(hashes)
    for case in cases.values():
        path = CASES / "input" / case["file"]
        assert sha256(path.read_bytes()).hexdigest() == hashes[path.name]
        book = load_workbook(path, data_only=False)
        try:
            assert book.sheetnames == ["Заявка", "Диагнозы", "Покупки", "Связи"]
            assert list(book["Заявка"].values) == [("Поле", "Значение"), ("ID заявки", case["id"])]
            diagnoses = list(book["Диагнозы"].values)
            purchases = list(book["Покупки"].values)
            links = list(book["Связи"].values)
            assert diagnoses[0] == ("ID диагноза", "Диагноз", "Код МКБ-10", "Возраст")
            assert purchases[0] == ("ID позиции", "Препарат", "Дозировка", "Количество", "Сумма", "Валюта")
            assert links[0] == ("ID позиции", "ID диагноза")
            assert [list(row) for row in diagnoses[1:]] == case["diagnoses"]
            assert [list(row) for row in purchases[1:]] == case["purchases"]
            assert [list(row) for row in links[1:]] == case["links"]
            assert len(diagnoses) - 1 == case["diagnosis_count"]
            assert all(cell.data_type != "f" for sheet in book for row in sheet for cell in row)
            totals = {}
            for row in purchases[1:]:
                totals[row[5]] = totals.get(row[5], Decimal("0")) + Decimal(str(row[4]))
            assert totals == {key: Decimal(value) for key, value in case["unique_purchase_totals"].items()}
            diagnosis_ids = [row[0] for row in diagnoses[1:]]
            item_ids = [row[0] for row in purchases[1:]]
            if case["expected_input_error"] is None:
                assert len(set(diagnosis_ids)) == len(diagnosis_ids)
                assert len(set(item_ids)) == len(item_ids)
                assert len(set(links[1:])) == len(links) - 1
                assert all(item in item_ids and diagnosis in diagnosis_ids for item, diagnosis in links[1:])
            elif case["expected_input_error"] == "UNKNOWN_DIAGNOSIS_LINK":
                assert any(diagnosis not in diagnosis_ids for _, diagnosis in links[1:])
            else:
                assert case["expected_input_error"] == "DUPLICATE_ITEM_ID"
                assert len(set(item_ids)) < len(item_ids)
        finally:
            book.close()
    two = cases["two"]
    assert two["links"] == [["P1", "D1"], ["P1", "D2"], ["P2", "D2"]]
    assert two["unique_purchase_totals"] == {"RUB": "300.30"}
    assert cases["missing_condition"]["diagnoses"][0][3] is None
    assert cases["currencies"]["unique_purchase_totals"] == {"RUB": "100.10", "USD": "2.20"}


def test_previous_independent_workbooks_are_unchanged():
    previous = ROOT / "excel_cases" / "review_20261112"
    hashes = json.loads((previous / "SHA256.json").read_text(encoding="utf-8"))
    assert len(hashes) == 10
    for name, digest in hashes.items():
        assert sha256((previous / "input" / name).read_bytes()).hexdigest() == digest
