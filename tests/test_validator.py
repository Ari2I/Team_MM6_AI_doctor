"""Проверки валидатора Excel-заявок."""

from decimal import Decimal
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from openpyxl import load_workbook

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

    assert codes(report) == [IssueCode.MISSING_COLUMN]
    assert [issue.column for issue in report.issues] == ["amount"]
    assert report.critical_count == 1


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
    assert "не учитывается в долях" in report.issues[0].message


def test_missing_quantity_is_ignored() -> None:
    report = validate_parse_result(result(item(quantity=None)))

    assert codes(report) == []
    assert report.is_valid is True


def test_invalid_number_parse_issue_is_warning_without_duplicate_empty_issue() -> None:
    parsed = result(item(amount=None))
    parsed.issues = [
        ParseIssue(code="INVALID_NUMBER", message="секретные данные", row=2, column="amount")
    ]

    report = validate_parse_result(parsed)

    assert codes(report) == [IssueCode.INVALID_NUMBER]
    assert report.issues[0].message == "Не удалось распознать сумму. Строка не учитывается в долях."
    assert "секретные данные" not in report.issues[0].message


@pytest.mark.parametrize("amount", [Decimal("0"), Decimal("-1")])
def test_non_positive_amount_is_warning(amount: Decimal) -> None:
    report = validate_parse_result(result(item(amount=amount)))

    assert codes(report) == [IssueCode.NON_POSITIVE_AMOUNT]
    assert report.is_valid is True
    assert "не учитывается в долях" in report.issues[0].message


def test_non_positive_quantity_is_ignored() -> None:
    report = validate_parse_result(result(item(quantity=Decimal("-1"))))

    assert codes(report) == []
    assert report.is_valid is True


def test_empty_dosage_is_ignored() -> None:
    report = validate_parse_result(result(item(dosage=None)))

    assert codes(report) == []
    assert report.is_valid is True


def test_missing_optional_dosage_and_quantity_columns_are_ignored() -> None:
    report = validate_parse_result(
        result(item(dosage=None, quantity=None), missing_columns=["dosage", "quantity"])
    )

    assert report.issues == []


def test_diagnosis_from_request_is_accepted_without_file_diagnosis() -> None:
    report = validate_parse_result(
        result(item(diagnosis=None), missing_columns=["diagnosis"]),
        request_diagnoses=["Диагноз из запроса"],
    )

    assert report.is_valid is True
    assert report.issues == []


@pytest.mark.parametrize("request_diagnoses", [None, [], ["", "  ", "\n"]])
def test_blank_request_diagnoses_do_not_replace_missing_diagnosis(
    request_diagnoses: list[str] | None,
) -> None:
    report = validate_parse_result(
        result(item(diagnosis=None), missing_columns=["diagnosis"]), request_diagnoses
    )

    assert codes(report) == [IssueCode.NO_DIAGNOSIS]
    assert report.is_valid is False


@pytest.mark.parametrize(
    ("file_diagnosis", "request_diagnoses", "expected_codes"),
    [
        (None, ["Диагноз А", "Диагноз Б"], [IssueCode.MULTIPLE_DIAGNOSES]),
        ("Диагноз А", ["Диагноз Б", "Диагноз В"], [IssueCode.MULTIPLE_DIAGNOSES]),
        ("Диагноз А", [" диагноз а ", "ДИАГНОЗ А", ""], []),
    ],
)
def test_distinct_diagnoses_from_both_sources_are_reported_once(
    file_diagnosis: str | None,
    request_diagnoses: list[str],
    expected_codes: list[IssueCode],
) -> None:
    report = validate_parse_result(result(item(diagnosis=file_diagnosis)), request_diagnoses)

    assert codes(report) == expected_codes
    assert report.is_valid is True


@pytest.mark.parametrize("remove_diagnosis", [False, True])
def test_excel_without_optional_columns(remove_diagnosis: bool) -> None:
    workbook = load_workbook(DATA_DIR / "sample_ok.xlsx")
    try:
        sheet = workbook.active
        assert sheet is not None
        # Удаляем справа налево, чтобы номера остальных столбцов не смещались.
        for column in [5, 3, 2] if remove_diagnosis else [3, 2]:
            sheet.delete_cols(column)
        buffer = BytesIO()
        workbook.save(buffer)
    finally:
        workbook.close()
    parsed = parse_excel(buffer.getvalue())
    before = parsed.model_dump()

    report = validate_parse_result(parsed, ["Учебный диагноз"] if remove_diagnosis else None)

    assert report.is_valid is True
    assert codes(report) == ([] if remove_diagnosis else [IssueCode.MULTIPLE_DIAGNOSES])
    assert all(row.dosage is row.quantity is None for row in parsed.items)
    assert parsed.model_dump() == before


@pytest.mark.parametrize(
    ("code", "column"),
    [
        ("INVALID_NUMBER", "quantity"),
        ("INVALID_QUANTITY", "quantity"),
        ("QUANTITY_INVALID", None),
        ("INVALID_DOSAGE", "dosage"),
        ("INVALID_DURATION", "duration"),
    ],
)
def test_unparsed_optional_values_do_not_produce_validation_warnings(
    code: str,
    column: str | None,
) -> None:
    parsed = result(item(quantity=None, dosage=None))
    parsed.issues = [ParseIssue(code=code, message="Содержимое ячейки", row=2, column=column)]
    before = parsed.model_dump()

    report = validate_parse_result(parsed)

    assert report.issues == []
    assert parsed.model_dump() == before


def test_missing_drug_name_column_is_critical() -> None:
    report = validate_parse_result(result(item(drug_name=None), missing_columns=["drug_name"]))

    assert codes(report) == [IssueCode.MISSING_COLUMN, IssueCode.EMPTY_DRUG_NAME]
    assert report.critical_count == 1
    assert report.issues[0].column == "drug_name"


def test_bad_amount_from_excel_is_one_warning_per_row() -> None:
    parsed = parse_excel((DATA_DIR / "sample_bad_types.xlsx").read_bytes())

    report = validate_parse_result(parsed)

    amount_issues = [issue for issue in report.issues if issue.column == "amount"]
    assert len(amount_issues) == 1
    assert amount_issues[0].code is IssueCode.INVALID_NUMBER
    assert amount_issues[0].level is IssueLevel.WARNING
    assert "не учитывается в долях" in amount_issues[0].message
    assert report.is_valid is True


def test_empty_amount_and_non_positive_amount_explain_exclusion_from_shares() -> None:
    report = validate_parse_result(
        result(item(amount=None), item(row_number=3, amount=Decimal("0")))
    )

    assert codes(report) == [IssueCode.EMPTY_AMOUNT, IssueCode.NON_POSITIVE_AMOUNT]
    assert all("не учитывается в долях" in issue.message for issue in report.issues)


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
