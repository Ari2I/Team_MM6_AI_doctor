"""Локальный разбор первого листа XLSX без проверки бизнес-правил."""

import re
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import BinaryIO
from unicodedata import category

import pandas as pd

from app.column_synonyms import COLUMN_SYNONYMS, HEADER_SEARCH_ROWS
from app.schemas.parsing import ParsedItem, ParseIssue, ParseResult

_NUMBER_PATTERN = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


class ExcelParseError(Exception):
    """Файл не удалось открыть как книгу XLSX."""


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
    row: tuple[object, ...], row_number: int, columns: dict[str, int], issues: list[ParseIssue]
) -> ParsedItem:
    values: dict[str, str | Decimal | None] = {}
    for field, index in columns.items():
        value = row[index]
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
    return ParsedItem(row_number=row_number, **values)


def parse_excel(source: bytes | BinaryIO) -> ParseResult:
    """Прочитать первый лист XLSX из байтов или открытого бинарного файла.

    Если книга открылась, но заголовки не распознаны, вернуть HEADER_NOT_FOUND
    и все канонические поля в missing_columns. Содержимое ячеек не логируется.
    """
    stream = BytesIO(source) if isinstance(source, bytes) else source
    try:
        with pd.ExcelFile(stream, engine="openpyxl") as workbook:
            if not workbook.sheet_names:
                raise ExcelParseError("В файле Excel нет доступных листов.")
            frame = pd.read_excel(
                workbook, sheet_name=0, header=None, dtype=object, na_filter=False
            )
    except ExcelParseError:
        raise
    except Exception:
        # Ошибки чтения ZIP, XML и книги имеют разные типы; наружу не передаём
        # исходное исключение, поскольку оно может содержать данные заявки.
        raise ExcelParseError(
            "Не удалось открыть файл Excel. Передайте исправный файл XLSX."
        ) from None

    result = ParseResult()
    synonyms = {
        normalize_header(alias): field
        for field, aliases in COLUMN_SYNONYMS.items()
        for alias in aliases
    }
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
    for row_number, row in enumerate(
        frame.iloc[header_index + 1 :].itertuples(index=False, name=None), start=header_index + 2
    ):
        if all(_clean_text(value) is None for value in row):
            continue
        result.items.append(_parse_item(row, row_number, columns, result.issues))
    return result
