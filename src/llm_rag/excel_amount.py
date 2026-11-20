"""Чтение точных числовых значений из XML листа XLSX."""

from decimal import Decimal, InvalidOperation

from openpyxl.utils.cell import coordinate_to_tuple
from openpyxl.xml.functions import iterparse
from pydantic import TypeAdapter

from llm_rag.contracts import PurchaseMoney


_AMOUNT = TypeAdapter(PurchaseMoney)
_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def numeric_cells(sheet, max_rows, columns):
    """Вернуть номера строк, столбцов и исходную запись выбранных числовых ячеек."""
    # ReadOnlyWorksheet уже разрешила связь листа с его файлом внутри архива.
    with sheet._get_source() as source:
        row_number = 0
        for _, element in iterparse(source, events=("end",)):
            if element.tag != _MAIN + "row":
                continue
            next_row = int(element.get("r", row_number + 1))
            if not row_number < next_row <= max_rows:
                raise ValueError(f"Строка вне допустимого диапазона на листе «{sheet.title}».")
            row_number = next_row
            column = 0
            for cell in element.iterfind(_MAIN + "c"):
                coordinate = cell.get("r")
                column = coordinate_to_tuple(coordinate)[1] if coordinate else column + 1
                if column not in columns or cell.get("t", "n") != "n":
                    continue
                raw = cell.findtext(_MAIN + "v")
                if raw:
                    yield row_number, column, raw
            element.clear()


def check_numeric_amounts(sheet, max_rows, *, legacy_quantities=None):
    """Проверить суммы и при чтении двухлистовой книги сохранить точные количества."""
    columns = (5,) if legacy_quantities is None else (4, 5)
    for row_number, column, raw in numeric_cells(sheet, max_rows, columns):
        if column == 4:
            legacy_quantities[row_number] = raw
            continue
        if len(raw) > 32:
            raise ValueError("Слишком длинная числовая запись суммы в XLSX.")
        try:
            _AMOUNT.validate_python(Decimal(raw))
        except (ValueError, InvalidOperation):
            raise ValueError("Числовая сумма в XLSX выходит за пределы диапазона или точности.") from None
