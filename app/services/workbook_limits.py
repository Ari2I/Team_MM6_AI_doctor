"""Проверка физических координат до разворачивания листа в DataFrame."""

from xml.etree.ElementTree import fromstring, iterparse
from zipfile import ZipFile

from openpyxl.utils.cell import coordinate_to_tuple


MAX_SHEET_ROWS = 2200
MAX_SHEET_COLUMNS = 64
TYPES_NS = "{http://schemas.openxmlformats.org/package/2006/content-types}"
SHEET_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


class WorkbookTooLarge(ValueError):
    """Координаты ячейки или число строк превышают размеры шаблона демо."""


def check_sheet_bounds(archive: ZipFile) -> None:
    manifest = fromstring(archive.read("[Content_Types].xml"))
    parts = {entry.filename for entry in archive.infolist()
             if entry.filename.startswith("xl/worksheets/") and entry.filename.endswith(".xml")
             and "/_rels/" not in entry.filename}
    parts.update(node.attrib["PartName"].lstrip("/") for node in manifest.findall(f"{TYPES_NS}Override")
                 if node.attrib.get("ContentType", "").endswith(".worksheet+xml"))
    for part in parts:
        with archive.open(part) as stream:
            row = column = row_count = cell_count = 0
            for event, node in iterparse(stream, events=("start", "end")):
                if event == "end":
                    node.clear()
                    continue
                if node.tag == f"{SHEET_NS}row":
                    row_count += 1
                    row = int(node.attrib.get("r", row + 1))
                    column = cell_count = 0
                elif node.tag == f"{SHEET_NS}c":
                    cell_count += 1
                    reference = node.attrib.get("r")
                    if reference:
                        row, column = coordinate_to_tuple(reference)
                    else:
                        column += 1
                if (max(row, row_count) > MAX_SHEET_ROWS
                        or max(column, cell_count) > MAX_SHEET_COLUMNS):
                    raise WorkbookTooLarge()
