"""Проверки валидатора Excel-заявок."""

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.schemas.parsing import ParsedItem, ParseIssue, ParseResult
from app.schemas.validation import IssueCode, IssueLevel, ValidationReport
from app.services import validator
from app.services.excel_parser import parse_excel
from app.services.validator import validate_file, validate_parse_result

DATA_DIR = Path(__file__).resolve().parent / "data"


def item(
    row_number: int = 2,
    *,
    drug_name: str | None = "Учебный препарат",
    dosage: str | None = "5 мг",
    quantity: Decimal | None = Decimal("1"),
    amount: Decimal | None = Decimal("100"),
    diagnosis: str | None = "Учебный диагноз",
) -> ParsedItem:
    return ParsedItem(
        row_number=row_number,
        drug_name=drug_name,
        dosage=dosage,
        quantity=quantity,
        amount=amount,
        diagnosis=diagnosis,
    )


def result(*items: ParsedItem, missing_columns: list[str] | None = None) -> ParseResult:
    return ParseResult(items=list(items), missing_columns=missing_columns or [])


def codes(report: ValidationReport) -> list[IssueCode]:
    return [issue.code for issue in report.issues]


def test_valid_file_without_warnings() -> None:
    parsed = parse_excel((DATA_DIR / "sample_ok.xlsx").read_bytes())
    parsed.items = [item(diagnosis="Один учебный диагноз") for _ in [0]]

    report = validate_parse_result(parsed)

    assert report.is_valid is True
    assert report.issues == []
    assert report.critical_count == report.warning_count == 0


def test_sample_with_multiple_diagnoses_is_accepted_with_one_warning() -> None:
    report = validate_parse_result(parse_excel((DATA_DIR / "sample_ok.xlsx").read_bytes()))

    assert report.is_valid is True
    assert codes(report) == [IssueCode.MULTIPLE_DIAGNOSES]
    assert report.warning_count == 1
    assert report.issues[0].row is None
    assert report.issues[0].column == "diagnosis"


def test_invalid_extension_is_critical() -> None:
    report = validate_file("заявка.xls", 1)

    assert report.is_valid is False
    assert codes(report) == [IssueCode.INVALID_EXTENSION]
    assert report.issues[0].level is IssueLevel.CRITICAL


def test_file_size_limit_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        validator,
        "get_validation_settings",
        lambda: SimpleNamespace(max_excel_size_bytes=100),
    )

    report = validate_file("заявка.xlsx", 101)

    assert codes(report) == [IssueCode.FILE_TOO_LARGE]
    assert report.is_valid is False


def test_missing_column_is_one_critical_issue_per_column() -> None:
    report = validate_parse_result(result(item(), missing_columns=["amount", "diagnosis"]))

    assert codes(report) == [IssueCode.MISSING_COLUMN, IssueCode.MISSING_COLUMN]
    assert [issue.column for issue in report.issues] == ["amount", "diagnosis"]
    assert report.critical_count == 2


def test_no_data_is_critical() -> None:
    report = validate_parse_result(ParseResult())

    assert IssueCode.NO_DATA in codes(report)
    assert report.critical_count == 2
    assert report.is_valid is False


def test_no_diagnosis_is_critical() -> None:
    report = validate_parse_result(result(item(diagnosis=None)))

    assert codes(report) == [IssueCode.NO_DIAGNOSIS]
    assert report.issues[0].level is IssueLevel.CRITICAL
    assert report.is_valid is False


def test_empty_drug_name_is_warning() -> None:
    report = validate_parse_result(result(item(drug_name=None)))

    assert codes(report) == [IssueCode.EMPTY_DRUG_NAME]
    assert report.is_valid is True
    assert report.issues[0].row == 2
    assert report.issues[0].column == "drug_name"


def test_empty_amount_is_warning() -> None:
    report = validate_parse_result(result(item(amount=None)))

    assert codes(report) == [IssueCode.EMPTY_AMOUNT]
    assert report.is_valid is True


def test_empty_quantity_is_warning() -> None:
    report = validate_parse_result(result(item(quantity=None)))

    assert codes(report) == [IssueCode.EMPTY_QUANTITY]
    assert report.is_valid is True


def test_invalid_number_parse_issue_is_warning_without_duplicate_empty_issue() -> None:
    parsed = result(item(amount=None))
    parsed.issues = [
        ParseIssue(code="INVALID_NUMBER", message="секретные данные", row=2, column="amount")
    ]

    report = validate_parse_result(parsed)

    assert codes(report) == [IssueCode.INVALID_NUMBER]
    assert report.issues[0].message == "Не удалось распознать число в указанном столбце."
    assert "секретные данные" not in report.issues[0].message


def test_non_positive_amount_is_warning() -> None:
    report = validate_parse_result(result(item(amount=Decimal("0"))))

    assert codes(report) == [IssueCode.NON_POSITIVE_AMOUNT]
    assert report.is_valid is True


def test_non_positive_quantity_is_warning() -> None:
    report = validate_parse_result(result(item(quantity=Decimal("-1"))))

    assert codes(report) == [IssueCode.NON_POSITIVE_QUANTITY]
    assert report.is_valid is True


def test_empty_dosage_is_warning() -> None:
    report = validate_parse_result(result(item(dosage=None)))

    assert codes(report) == [IssueCode.EMPTY_DOSAGE]
    assert report.is_valid is True


def test_empty_diagnosis_is_warning_when_another_row_has_diagnosis() -> None:
    report = validate_parse_result(
        result(item(row_number=2), item(row_number=3, diagnosis=None, amount=Decimal("101")))
    )

    assert codes(report) == [IssueCode.EMPTY_DIAGNOSIS]
    assert report.issues[0].row == 3


def test_multiple_diagnoses_are_one_informational_warning() -> None:
    report = validate_parse_result(
        result(
            item(diagnosis="Диагноз А"),
            item(row_number=3, diagnosis="Диагноз Б", amount=Decimal("101")),
        )
    )

    assert codes(report) == [IssueCode.MULTIPLE_DIAGNOSES]
    assert report.warning_count == 1
    assert "Диагноз А" not in report.issues[0].message
    assert "Диагноз Б" not in report.issues[0].message


def test_duplicate_rows_are_warnings() -> None:
    report = validate_parse_result(result(item(row_number=2), item(row_number=3)))

    assert codes(report) == [IssueCode.DUPLICATE_ROW]
    assert report.issues[0].row == 3
    assert report.is_valid is True


def test_other_parse_issue_is_transferred_as_warning() -> None:
    parsed = result(item())
    parsed.issues = [ParseIssue(code="DUPLICATE_COLUMN", message="лишнее", row=1, column="amount")]

    report = validate_parse_result(parsed)

    assert codes(report) == [IssueCode.PARSE_ISSUE]
    assert report.issues[0].level is IssueLevel.WARNING
    assert report.issues[0].message == "В файле обнаружен повторяющийся столбец."
