"""Локальный разбор первого листа XLSX без проверки бизнес-правил."""

import re
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import BinaryIO
from unicodedata import category

import pandas as pd

from app.column_synonyms import COLUMN_SYNONYMS, HEADER_SEARCH_ROWS
from app.schemas.parsing import ParsedDemoItem, ParsedItem, ParseIssue, ParseResult

_NUMBER_PATTERN = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


class ExcelParseError(Exception):
    """Файл не удалось открыть как книгу XLSX."""


class ExcelLayoutChoice(ExcelParseError):
    """Для разбора нужно уточнить лист или сопоставление колонок."""

    def __init__(self, *, sheets=None, columns=None, sheet=None):
        super().__init__("Уточните таблицу и столбцы заявки.")
        self.options = {"sheets": sheets or [], "columns": columns or {}, "sheet": sheet}


def _clean_text(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    return " ".join(str(value).split()) or None


def normalize_header(value: object) -> str:
    """Убрать лишние пробельные символы и привести заголовок к нижнему регистру."""
    return (_clean_text(value) or "").lower()


def parse_number(value: object) -> Decimal | None:
    """Разобрать число; пустое или нераспознаваемое значение возвращает None."""
    if isinstance(value, bool):
        return None
    text = _clean_text(value)
    if text is None:
        return None

    text = "".join(text.split()).lower()
    # Символ валюты допустим перед числом или после него, «руб.» — после числа.
    if text and category(text[0]) == "Sc":
        text = text[1:]
    if text.endswith("руб."):
        text = text[:-4]
    elif text.endswith("руб"):
        text = text[:-3]
    elif text and category(text[-1]) == "Sc":
        text = text[:-1]
    text = text.replace(",", ".")
    if _NUMBER_PATTERN.fullmatch(text) is None:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _find_header(frame: pd.DataFrame, synonyms: dict[str, str]) -> int | None:
    """Выбрать максимум разных известных полей; при равенстве — первую строку."""
    best_row: int | None = None
    best_score = 0
    for index, row in enumerate(frame.head(HEADER_SEARCH_ROWS).itertuples(index=False, name=None)):
        fields = {
            synonyms[header] for value in row if (header := normalize_header(value)) in synonyms
        }
        if len(fields) > best_score:
            best_row, best_score = index, len(fields)
    return best_row


def _map_columns(
    headers: tuple[object, ...], synonyms: dict[str, str], row_number: int, result: ParseResult
) -> dict[str, int]:
    columns: dict[str, int] = {}
    for index, value in enumerate(headers):
        header = normalize_header(value)
        if not header:
            continue
        original = str(value)
        field = synonyms.get(header)
        if field is None:
            result.unknown_columns.append(original)
        elif field in columns:
            result.issues.append(
                ParseIssue(
                    code="DUPLICATE_COLUMN",
                    message="Столбец повторяется. Использован первый столбец слева.",
                    row=row_number,
                    column=field,
                )
            )
        else:
            columns[field] = index
            result.column_mapping[original] = field
    result.missing_columns = [field for field in COLUMN_SYNONYMS if field not in columns]
    return columns


def _parse_item(
    row: tuple[object, ...], row_number: int, columns: dict[str, int], issues: list[ParseIssue],
    *, preserve_cells: bool = False,
) -> ParsedItem:
    values: dict[str, str | Decimal | None] = {}
    original_cells = {}
    for field, index in columns.items():
        value = row[index]
        if preserve_cells:
            original_cells[field] = str(value) if _clean_text(value) is not None else None
        if field in ("quantity", "amount"):
            number = parse_number(value)
            values[field] = number
            if number is None and _clean_text(value) is not None:
                issues.append(
                    ParseIssue(
                        code="INVALID_NUMBER",
                        message="Не удалось распознать число в ячейке.",
                        row=row_number,
                        column=field,
                    )
                )
        else:
            values[field] = _clean_text(value)
    if preserve_cells:
        return ParsedDemoItem(row_number=row_number, original_cells=original_cells, **values)
    return ParsedItem(row_number=row_number, **values)


def _preview_sheet(workbook, name, synonyms):
    preview = pd.read_excel(workbook, sheet_name=name, header=None, dtype=object, na_filter=False,
                            nrows=HEADER_SEARCH_ROWS + 2)
    header = _find_header(preview, synonyms)
    if header is None:
        return None
    values = preview.iloc[header]
    fields = {synonyms[label] for value in values if (label := normalize_header(value)) in synonyms}
    if len(fields) != len(COLUMN_SYNONYMS):
        return None
    return {"name": name, "header_row": header + 1,
            "headers": [(_clean_text(value) or "")[:120] for value in values]}


def parse_excel(
    source: bytes | BinaryIO, *, demo: bool = False, diagnosis_blocks: bool = False,
    sheet: str | None = None, column_choices: dict[str, int] | None = None,
) -> ParseResult:
    """Прочитать первый лист XLSX из байтов или открытого бинарного файла.

    Если книга открылась, но заголовки не распознаны, вернуть HEADER_NOT_FOUND
    и все канонические поля в missing_columns. Содержимое ячеек не логируется.
    """
    stream = BytesIO(source) if isinstance(source, bytes) else source
    synonyms = {
        normalize_header(alias): field
        for field, aliases in COLUMN_SYNONYMS.items()
        for alias in aliases
    }
    try:
        engine_kwargs = {"data_only": False} if demo else None
        with pd.ExcelFile(stream, engine="openpyxl", engine_kwargs=engine_kwargs) as workbook:
            if not workbook.sheet_names:
                raise ExcelParseError("В файле Excel нет доступных листов.")
            if sheet is not None and sheet not in workbook.sheet_names:
                raise ExcelParseError("Выбранный лист отсутствует в книге.")
            if diagnosis_blocks and sheet is None and len(workbook.sheet_names) > 1:
                candidates = [candidate for name in workbook.sheet_names
                              if (candidate := _preview_sheet(workbook, name, synonyms)) is not None]
                if len(candidates) > 1:
                    raise ExcelLayoutChoice(sheets=candidates)
                if candidates:
                    sheet = candidates[0]["name"]
            sheet = sheet or workbook.sheet_names[0]
            frame = pd.read_excel(workbook, sheet_name=sheet, header=None, dtype=object, na_filter=False)
    except ExcelParseError:
        raise
    except Exception:
        # Ошибки чтения ZIP, XML и книги имеют разные типы; наружу не передаём
        # исходное исключение, поскольку оно может содержать данные заявки.
        raise ExcelParseError(
            "Не удалось открыть файл Excel. Передайте исправный файл XLSX."
        ) from None

    result = ParseResult(sheet_name=sheet)
    header_index = _find_header(frame, synonyms)
    if header_index is None:
        result.missing_columns = list(COLUMN_SYNONYMS)
        result.issues.append(
            ParseIssue(
                code="HEADER_NOT_FOUND",
                message="Не удалось найти заголовки таблицы в первых десяти строках листа.",
            )
        )
        return result

    headers = tuple(frame.iloc[header_index])
    columns = _map_columns(headers, synonyms, header_index + 1, result)
    if diagnosis_blocks:
        duplicated = {issue.column for issue in result.issues if issue.code == "DUPLICATE_COLUMN"}
        if duplicated:
            options = {field: [
                {"index": index, "header": _clean_text(value),
                 "sample": [(_clean_text(cell) or "")[:100]
                            for cell in frame.iloc[header_index + 1:header_index + 3, index]]}
                for index, value in enumerate(headers) if synonyms.get(normalize_header(value)) == field
            ] for field in COLUMN_SYNONYMS if field in duplicated}
            choices = column_choices or {}
            if any(choices.get(field) not in {option["index"] for option in variants}
                   for field, variants in options.items()):
                raise ExcelLayoutChoice(columns=options, sheet=sheet)
            for field in duplicated:
                columns[field] = choices[field]
            result.issues = [issue for issue in result.issues if issue.code != "DUPLICATE_COLUMN"]
    numeric_columns = {field: index for field, index in columns.items() if field in ("amount", "quantity")}
    formula_cells = {
        (number, field): (index, row[index])
        for number, row in enumerate(frame.iloc[header_index + 1:].itertuples(index=False, name=None),
                                     header_index + 2)
        for field, index in numeric_columns.items()
        if diagnosis_blocks and isinstance(row[index], str) and row[index].startswith("=")
    }
    cached = None
    if formula_cells:
        stream.seek(0)
        cached = pd.read_excel(stream, engine="openpyxl", engine_kwargs={"data_only": True},
                               sheet_name=sheet, header=None, dtype=object, na_filter=False)
    block_start = 0
    for row_number, row in enumerate(
        frame.iloc[header_index + 1 :].itertuples(index=False, name=None), start=header_index + 2
    ):
        if all(_clean_text(value) is None for value in row):
            continue
        if demo and columns and all(
            synonyms.get(normalize_header(row[index])) == field for field, index in columns.items()
        ):
            result.issues.append(ParseIssue(
                code="REPEATED_HEADER", row=row_number, message="Повтор строки заголовка пропущен.",
            ))
            continue
        original_formulas = {}
        if cached is not None:
            updated = list(row)
            for field in numeric_columns:
                cell = formula_cells.get((row_number, field))
                if cell is not None:
                    index, original_formulas[field] = cell
                    value = cached.iat[row_number - 1, index] if row_number <= len(cached) and index < cached.shape[1] else None
                    updated[index] = value
                    if field == "amount" and _clean_text(value) is None:
                        result.issues.append(ParseIssue(
                            code="AMOUNT_FORMULA_NO_CACHE", row=row_number, column="amount",
                            message="Формула суммы без сохранённого результата. Пересчитайте и сохраните XLSX.",
                        ))
            row = tuple(updated)
        item = _parse_item(row, row_number, columns, result.issues, preserve_cells=demo)
        if isinstance(item, ParsedDemoItem):
            item.original_cells.update(original_formulas)
        if diagnosis_blocks:
            if item.diagnosis:
                block_start = row_number
                result.diagnosis_blocks[block_start] = item.diagnosis
                if not any(_clean_text(row[index]) for field, index in columns.items() if field != "diagnosis"):
                    continue
            if block_start:
                item.diagnosis = result.diagnosis_blocks[block_start]
            else:
                result.diagnosis_blocks[0] = "Диагноз не указан"
            result.item_blocks[row_number] = block_start
        result.items.append(item)
    return result
