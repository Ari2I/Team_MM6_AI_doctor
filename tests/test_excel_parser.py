"""Проверки парсинга на локальных вымышленных файлах из tests/data/."""

from decimal import Decimal
from io import BytesIO
from pathlib import Path

import pytest

from app.schemas.parsing import ParsedItem
from app.services.excel_parser import ExcelParseError, normalize_header, parse_excel, parse_number

DATA_DIR = Path(__file__).resolve().parent / "data"
FIELDS = ["drug_name", "dosage", "quantity", "amount", "diagnosis"]
EXPECTED_MAPPING = {
    "Торговое наименование": "drug_name",
    "Дозировка": "dosage",
    "Количество": "quantity",
    "Сумма": "amount",
    "Диагноз (из заключения)": "diagnosis",
}


def test_parse_correct_file() -> None:
    result = parse_excel((DATA_DIR / "sample_ok.xlsx").read_bytes())

    assert result.column_mapping == EXPECTED_MAPPING
    assert result.missing_columns == result.unknown_columns == result.issues == []
    assert [item.row_number for item in result.items] == [2, 3, 4, 5, 6, 7]
    assert result.items[0].quantity == Decimal("2")
    assert result.items[0].amount == Decimal("1200")
    assert result.items[1].amount == Decimal("850.50")
    assert result.items[0].drug_name is not None
    assert result.items[0].dosage == "200 мг"
    assert result.items[0].diagnosis == "Учебный диагноз 01"


def test_file_object_is_supported_and_stays_open() -> None:
    content = (DATA_DIR / "sample_ok.xlsx").read_bytes()
    with (DATA_DIR / "sample_ok.xlsx").open("rb") as stream:
        stream.read(10)
        result = parse_excel(stream)
        assert not stream.closed
    memory_stream = BytesIO(content)
    assert parse_excel(memory_stream) == result == parse_excel(content)
    assert not memory_stream.closed


def test_messy_headers() -> None:
    result = parse_excel((DATA_DIR / "sample_messy_headers.xlsx").read_bytes())
    correct = parse_excel((DATA_DIR / "sample_ok.xlsx").read_bytes())

    assert result.items == correct.items
    assert result.issues == result.missing_columns == result.unknown_columns == []
    assert list(result.column_mapping.values()) == FIELDS
    assert "  тОРГОВОЕ   НАИМЕНОВАНИЕ  " in result.column_mapping


def test_missing_column() -> None:
    result = parse_excel((DATA_DIR / "sample_missing_column.xlsx").read_bytes())

    assert result.missing_columns == ["amount"]
    assert "amount" not in result.column_mapping.values()
    assert len(result.items) == 6
    assert all(item.amount is None for item in result.items)
    assert result.issues == []


def test_invalid_amount_creates_issue_and_keeps_other_rows() -> None:
    result = parse_excel((DATA_DIR / "sample_bad_types.xlsx").read_bytes())

    assert len(result.items) == 6
    assert result.items[0].amount is None
    assert result.items[0].quantity == Decimal("2")
    assert result.items[1].amount == Decimal("850.50")
    assert [issue.model_dump() for issue in result.issues] == [
        {
            "code": "INVALID_NUMBER",
            "message": "Не удалось распознать число в ячейке.",
            "row": 2,
            "column": "amount",
        }
    ]


def test_headers_only() -> None:
    result = parse_excel((DATA_DIR / "sample_empty.xlsx").read_bytes())

    assert result.items == result.issues == []
    assert result.column_mapping == EXPECTED_MAPPING
    assert result.missing_columns == result.unknown_columns == []


@pytest.mark.parametrize("filename", ["sample_corrupt.xlsx", "sample_parser_no_sheets.xlsx"])
def test_unreadable_workbook(filename: str) -> None:
    with pytest.raises(ExcelParseError) as error:
        parse_excel((DATA_DIR / filename).read_bytes())
    assert "Excel" in str(error.value)
    assert error.value.__suppress_context__ or error.value.__context__ is None


def test_zero_bytes() -> None:
    with pytest.raises(ExcelParseError, match="исправный файл XLSX"):
        parse_excel(b"")


def test_header_search_row_numbers_cleaning_and_first_sheet() -> None:
    result = parse_excel((DATA_DIR / "sample_parser_layout.xlsx").read_bytes())

    assert result.column_mapping == EXPECTED_MAPPING
    assert result.missing_columns == []
    assert result.unknown_columns == ["Примечание"]
    assert [item.row_number for item in result.items] == [11, 14, 15, 16]
    assert result.items[0] == ParsedItem(
        row_number=11,
        drug_name="Учебный препарат А",
        dosage="5 мг",
        quantity=Decimal("1234.50"),
        amount=Decimal("2345.60"),
        diagnosis="NA",
    )
    assert result.items[1].dosage is result.items[1].amount is result.items[1].diagnosis is None
    assert result.items[1].quantity == Decimal("-2")  # Бизнес-правила не проверяются.
    assert result.items[2] == ParsedItem(row_number=15)
    assert result.items[3].drug_name == "0"
    assert result.items[3].quantity == Decimal("0")
    assert result.items[3].amount == Decimal("-1.25")
    assert [(issue.code, issue.row, issue.column) for issue in result.issues] == [
        ("INVALID_NUMBER", 14, "amount")
    ]


def test_duplicate_columns_use_first_and_report_issue() -> None:
    result = parse_excel((DATA_DIR / "sample_parser_duplicates.xlsx").read_bytes())

    assert result.items[0].amount == Decimal("5")
    assert result.column_mapping == EXPECTED_MAPPING
    assert result.missing_columns == result.unknown_columns == []
    assert [(issue.code, issue.row, issue.column) for issue in result.issues] == [
        ("DUPLICATE_COLUMN", 1, "amount")
    ]


@pytest.mark.parametrize(
    "filename",
    ["sample_parser_no_headers.xlsx", "sample_parser_late_header.xlsx", "sample_parser_blank.xlsx"],
)
def test_no_recognized_header_in_first_ten_rows(filename: str) -> None:
    result = parse_excel((DATA_DIR / filename).read_bytes())

    assert result.items == []
    assert result.column_mapping == {}
    assert result.missing_columns == FIELDS
    assert [(issue.code, issue.row, issue.column) for issue in result.issues] == [
        ("HEADER_NOT_FOUND", None, None)
    ]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1 234,50", Decimal("1234.50")),
        ("1234.5", Decimal("1234.5")),
        ("5 руб.", Decimal("5")),
        ("", None),
        ("abc", None),
        ("1\u00a0234,50 ₽", Decimal("1234.50")),
        ("1\u202f234,50 РУБ.", Decimal("1234.50")),
        ("€ 5,25", Decimal("5.25")),
        ("5 $", Decimal("5")),
        ("1.234,50", None),
        ("1руб.2", None),
        ("NaN", None),
        ("Infinity", None),
        (True, None),
        (None, None),
        (" \n\u00a0 ", None),
        (0, Decimal("0")),
        (-2.5, Decimal("-2.5")),
        (Decimal("12.30"), Decimal("12.30")),
        ("0.100000000000000001", Decimal("0.100000000000000001")),
    ],
)
def test_parse_number(value: object, expected: Decimal | None) -> None:
    assert parse_number(value) == expected


def test_header_normalization() -> None:
    assert normalize_header("  ТОРГОВОЕ\u00a0\n НАИМЕНОВАНИЕ\t ") == "торговое наименование"


def test_item_fields_are_optional() -> None:
    assert ParsedItem(row_number=1).model_dump() == {
        "row_number": 1,
        "drug_name": None,
        "dosage": None,
        "quantity": None,
        "amount": None,
        "diagnosis": None,
    }
