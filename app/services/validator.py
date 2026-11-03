"""Проверка файла и результата разбора без БД и бизнес-анализа."""

from os import PathLike

from app.config import get_validation_settings
from app.schemas.parsing import ParsedItem, ParseIssue, ParseResult
from app.schemas.validation import (
    IssueCode,
    IssueLevel,
    ValidationIssue,
    ValidationReport,
)

_COLUMN_LABELS = {
    "drug_name": "торговое наименование",
    "dosage": "дозировка",
    "quantity": "количество",
    "amount": "сумма",
    "diagnosis": "диагноз",
}


def _issue(
    level: IssueLevel,
    code: IssueCode,
    message: str,
    row: int | None = None,
    column: str | None = None,
) -> ValidationIssue:
    return ValidationIssue(level=level, code=code, message=message, row=row, column=column)


def validate_file(filename: str | PathLike[str], size_bytes: int) -> ValidationReport:
    """Проверить расширение и размер загружаемого файла."""
    issues: list[ValidationIssue] = []
    filename_text = str(filename)
    if not filename_text.lower().endswith(".xlsx"):
        issues.append(
            _issue(
                IssueLevel.CRITICAL,
                IssueCode.INVALID_EXTENSION,
                "Допустим только файл Excel с расширением .xlsx.",
            )
        )
    if size_bytes > get_validation_settings().max_excel_size_bytes:
        issues.append(
            _issue(
                IssueLevel.CRITICAL,
                IssueCode.FILE_TOO_LARGE,
                "Размер файла превышает допустимый лимит.",
            )
        )
    return ValidationReport(issues=issues)


def _parse_issue_to_validation(issue: ParseIssue) -> ValidationIssue:
    parse_code = issue.code.upper()
    if parse_code in {
        "INVALID_NUMBER",
        "INVALID_AMOUNT",
        "AMOUNT_INVALID",
        "INVALID_QUANTITY",
        "QUANTITY_INVALID",
    }:
        message = "Не удалось распознать число в указанном столбце."
        code = IssueCode.INVALID_NUMBER
    elif parse_code == "DUPLICATE_COLUMN":
        message = "В файле обнаружен повторяющийся столбец."
        code = IssueCode.PARSE_ISSUE
    elif parse_code == "HEADER_NOT_FOUND":
        message = "Не удалось определить заголовки таблицы."
        code = IssueCode.PARSE_ISSUE
    else:
        message = "При разборе файла обнаружена ошибка."
        code = IssueCode.PARSE_ISSUE
    return _issue(IssueLevel.WARNING, code, message, issue.row, issue.column)


def _has_value(value: object) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def _field_value(item: ParsedItem, field: str) -> object:
    return getattr(item, field)


def _add_numeric_issues(
    item: ParsedItem,
    field: str,
    empty_code: IssueCode,
    non_positive_code: IssueCode,
    issues: list[ValidationIssue],
    invalid_fields: set[tuple[int, str]],
) -> None:
    value = _field_value(item, field)
    label = _COLUMN_LABELS[field]
    if (item.row_number, field) in invalid_fields:
        return
    if value is None:
        issues.append(
            _issue(
                IssueLevel.WARNING,
                empty_code,
                f"Не указано значение в столбце «{label}».",
                item.row_number,
                field,
            )
        )
    elif value <= 0:
        issues.append(
            _issue(
                IssueLevel.WARNING,
                non_positive_code,
                f"Значение в столбце «{label}» должно быть больше нуля.",
                item.row_number,
                field,
            )
        )


def _duplicate_items(items: list[ParsedItem]) -> list[int]:
    seen: dict[tuple[object, ...], int] = {}
    duplicates: list[int] = []
    for item in items:
        key = (item.drug_name, item.dosage, item.quantity, item.amount)
        first_row = seen.setdefault(key, item.row_number)
        if first_row != item.row_number:
            duplicates.append(item.row_number)
    return duplicates


def validate_parse_result(parse_result: ParseResult) -> ValidationReport:
    """Проверить структурный результат парсера и вернуть русские сообщения."""
    issues: list[ValidationIssue] = []
    for column in parse_result.missing_columns:
        label = _COLUMN_LABELS.get(column, column)
        issues.append(
            _issue(
                IssueLevel.CRITICAL,
                IssueCode.MISSING_COLUMN,
                f"Не найден обязательный столбец «{label}».",
                column=column,
            )
        )

    if not parse_result.items:
        issues.append(
            _issue(IssueLevel.CRITICAL, IssueCode.NO_DATA, "В заявке нет строк с данными.")
        )

    if not any(_has_value(item.diagnosis) for item in parse_result.items):
        issues.append(
            _issue(
                IssueLevel.CRITICAL,
                IssueCode.NO_DIAGNOSIS,
                "Диагноз не указан ни в одной строке.",
            )
        )

    invalid_fields: set[tuple[int, str]] = set()
    for parse_issue in parse_result.issues:
        validation_issue = _parse_issue_to_validation(parse_issue)
        issues.append(validation_issue)
        if (
            parse_issue.code.upper()
            in {
                "INVALID_NUMBER",
                "INVALID_AMOUNT",
                "AMOUNT_INVALID",
                "INVALID_QUANTITY",
                "QUANTITY_INVALID",
            }
            and parse_issue.row is not None
        ):
            invalid_fields.add((parse_issue.row, parse_issue.column or ""))

    for item in parse_result.items:
        if not _has_value(item.drug_name):
            issues.append(
                _issue(
                    IssueLevel.WARNING,
                    IssueCode.EMPTY_DRUG_NAME,
                    "Не указано название препарата.",
                    item.row_number,
                    "drug_name",
                )
            )
        _add_numeric_issues(
            item,
            "amount",
            IssueCode.EMPTY_AMOUNT,
            IssueCode.NON_POSITIVE_AMOUNT,
            issues,
            invalid_fields,
        )
        _add_numeric_issues(
            item,
            "quantity",
            IssueCode.EMPTY_QUANTITY,
            IssueCode.NON_POSITIVE_QUANTITY,
            issues,
            invalid_fields,
        )
        if not _has_value(item.dosage):
            issues.append(
                _issue(
                    IssueLevel.WARNING,
                    IssueCode.EMPTY_DOSAGE,
                    "Не указана дозировка.",
                    item.row_number,
                    "dosage",
                )
            )
        if not _has_value(item.diagnosis) and any(
            _has_value(other.diagnosis) for other in parse_result.items
        ):
            issues.append(
                _issue(
                    IssueLevel.WARNING,
                    IssueCode.EMPTY_DIAGNOSIS,
                    "Не указан диагноз в строке.",
                    item.row_number,
                    "diagnosis",
                )
            )

    diagnoses = {
        str(item.diagnosis).strip().casefold()
        for item in parse_result.items
        if _has_value(item.diagnosis)
    }
    if len(diagnoses) > 1:
        issues.append(
            _issue(
                IssueLevel.WARNING,
                IssueCode.MULTIPLE_DIAGNOSES,
                "В заявке указано несколько разных диагнозов.",
                column="diagnosis",
            )
        )

    for row_number in _duplicate_items(parse_result.items):
        issues.append(
            _issue(
                IssueLevel.WARNING,
                IssueCode.DUPLICATE_ROW,
                "Обнаружена строка с повторяющимися названием, дозировкой, количеством и суммой.",
                row_number,
            )
        )

    return ValidationReport(issues=issues)
