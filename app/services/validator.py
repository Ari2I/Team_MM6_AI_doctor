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


def _parse_issue_to_validation(issue: ParseIssue) -> ValidationIssue | None:
    parse_code = issue.code.upper()
    # Структурные замечания сохраняются, значения этих столбцов не оцениваем.
    if parse_code in {"INVALID_QUANTITY", "QUANTITY_INVALID"} or (
        issue.column in {"dosage", "quantity", "duration"} and parse_code != "DUPLICATE_COLUMN"
    ):
        return None
    if parse_code in {
        "INVALID_NUMBER",
        "INVALID_AMOUNT",
        "AMOUNT_INVALID",
    }:
        if issue.column == "amount":
            message = "Не удалось распознать сумму. Строка не учитывается в долях."
        else:
            message = "Не удалось распознать число в указанном столбце."
        code = IssueCode.INVALID_NUMBER
    elif parse_code in {"REPEATED_HEADER", "AMOUNT_FORMULA_NO_CACHE"}:
        message = issue.message
        code = IssueCode.PARSE_ISSUE
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


def _add_amount_issues(
    item: ParsedItem,
    issues: list[ValidationIssue],
    invalid_rows: set[int],
) -> None:
    if item.row_number in invalid_rows:
        return
    if item.amount is None:
        issues.append(
            _issue(
                IssueLevel.WARNING,
                IssueCode.EMPTY_AMOUNT,
                "Не указана сумма. Строка не учитывается в долях.",
                item.row_number,
                "amount",
            )
        )
    elif item.amount <= 0:
        issues.append(
            _issue(
                IssueLevel.WARNING,
                IssueCode.NON_POSITIVE_AMOUNT,
                "Сумма должна быть больше нуля. Строка не учитывается в долях.",
                item.row_number,
                "amount",
            )
        )


def _duplicate_items(items: list[ParsedItem], item_blocks: dict[int, int]) -> list[int]:
    seen: dict[tuple[object, ...], int] = {}
    duplicates: list[int] = []
    for item in items:
        key = (item_blocks.get(item.row_number), item.drug_name, item.dosage, item.quantity, item.amount)
        first_row = seen.setdefault(key, item.row_number)
        if first_row != item.row_number:
            duplicates.append(item.row_number)
    return duplicates


def validate_parse_result(
    parse_result: ParseResult, request_diagnoses: list[str] | None = None, *, demo: bool = False,
    blocked: bool = False,
) -> ValidationReport:
    """Проверить результат парсера без изменения исходных значений.

    Диагнозы запроса относятся ко всей заявке. Пустые строки не считаются
    диагнозами; повторы после strip/casefold учитываются один раз.
    """
    issues: list[ValidationIssue] = []
    for column in parse_result.missing_columns:
        if not blocked and column in {"dosage", "quantity", "diagnosis"}:
            continue
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

    file_diagnoses = {
        str(item.diagnosis).strip().casefold()
        for item in parse_result.items
        if _has_value(item.diagnosis)
    }
    external_diagnoses = {
        diagnosis.strip().casefold()
        for diagnosis in (request_diagnoses or [])
        if _has_value(diagnosis)
    }
    diagnoses = external_diagnoses if demo else file_diagnoses | external_diagnoses
    if not diagnoses and not blocked:
        issues.append(
            _issue(
                IssueLevel.CRITICAL,
                IssueCode.NO_DIAGNOSIS,
                "Диагноз не указан ни в файле, ни в запросе.",
            )
        )

    invalid_amount_rows: set[int] = set()
    for parse_issue in parse_result.issues:
        validation_issue = _parse_issue_to_validation(parse_issue)
        if validation_issue is not None:
            issues.append(validation_issue)
        if (
            parse_issue.code.upper()
            in {
                "INVALID_NUMBER",
                "INVALID_AMOUNT",
                "AMOUNT_INVALID",
                "AMOUNT_FORMULA_NO_CACHE",
            }
            and parse_issue.column == "amount"
            and parse_issue.row is not None
        ):
            invalid_amount_rows.add(parse_issue.row)

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
        _add_amount_issues(item, issues, invalid_amount_rows)
        if (blocked and parse_result.item_blocks.get(item.row_number) == 0
                or not blocked and not _has_value(item.diagnosis) and file_diagnoses and not external_diagnoses):
            issues.append(
                _issue(
                    IssueLevel.WARNING,
                    IssueCode.EMPTY_DIAGNOSIS,
                    "Не указан диагноз в строке.",
                    item.row_number,
                    "diagnosis",
                )
            )

    if len(diagnoses) > 1:
        issues.append(
            _issue(
                IssueLevel.WARNING,
                IssueCode.MULTIPLE_DIAGNOSES,
                "В заявке указано несколько разных диагнозов.",
                column="diagnosis",
            )
        )

    for row_number in _duplicate_items(parse_result.items, parse_result.item_blocks):
        issues.append(
            _issue(
                IssueLevel.WARNING,
                IssueCode.DUPLICATE_ROW,
                "Обнаружена строка с повторяющимися названием, дозировкой, количеством и суммой.",
                row_number,
            )
        )

    return ValidationReport(issues=issues)
