"""Многодиагнозный Excel → фасад → сохранённые JSON и HTML одной заявки."""

from io import BytesIO
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZipFile, BadZipFile
from zlib import error as CompressionError

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from pydantic import ValidationError

from llm_rag.integration_contracts import IntegrationRequest, IntegrationResult
from llm_rag.excel_amount import check_numeric_amounts
from llm_rag.human_report import render_human_report


HEADERS = {
    "Заявка": ["Поле", "Значение"],
    "Диагнозы": ["ID диагноза", "Диагноз", "Код МКБ-10", "Возраст"],
    "Покупки": ["ID позиции", "Препарат", "Дозировка", "Количество", "Сумма", "Валюта"],
    "Связи": ["ID позиции", "ID диагноза"],
}

DIAGNOSIS_OPTIONAL_HEADERS = ("Возрастная группа", "Редакция КР")


@dataclass(frozen=True, repr=False)
class IntegrationWorkbook:
    request: IntegrationRequest
    rows_by_item_id: dict[str, int]


def _decimal(value):
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("Логическое значение недопустимо вместо числа.")
    return str(value)


def read_integration_card(path: Path) -> IntegrationRequest:
    """Проверить файл до обработки; формулы, внешние ссылки и лишние поля не использовать."""
    path = Path(path)
    if path.stat().st_size > 10_000_000:
        raise ValueError("Размер XLSX превышает 10 МБ.")
    return read_integration_bytes(path.read_bytes())


def read_integration_bytes(content: bytes) -> IntegrationRequest:
    """The same parser for HTTP bytes and the standalone CLI; no temporary file."""
    return read_integration_workbook(content).request


def read_integration_workbook(content: bytes) -> IntegrationWorkbook:
    if len(content) > 10_000_000:
        raise ValueError("Размер XLSX превышает 10 МБ.")
    try:
        return _read_content(Path("upload.xlsx"), content)
    except (
        BadZipFile, CompressionError, EOFError, InvalidFileException, LookupError,
        NotImplementedError, OverflowError, SyntaxError, TypeError,
    ):
        # Общие строки и другие части книги могут читаться лениво при обходе листов.
        raise ValueError("Не удалось прочитать структуру XLSX.") from None


def _read_content(path: Path, content: bytes) -> IntegrationWorkbook:
    with ZipFile(BytesIO(content)) as archive:
        if sum(info.file_size for info in archive.infolist()) > 100_000_000:
            raise ValueError("Распакованный XLSX превышает 100 МБ.")
        if any(info.flag_bits & 1 for info in archive.infolist()):
            raise ValueError("Зашифрованные книги XLSX не поддерживаются.")
    book = load_workbook(BytesIO(content), read_only=True, data_only=False, keep_links=False)
    try:
        if set(book.sheetnames) == {"Заявка", "Покупки"}:
            from llm_rag.excel_scenario import read_card

            card = read_card(path, content=content)
            request = card.request
            value = IntegrationRequest.model_validate(
                dict(
                    schema_version="integration-request.1",
                    request_id=request.request_id,
                    diagnoses=[
                        dict(
                            diagnosis_id=card.diagnosis_id,
                            name=request.diagnosis,
                            icd10=request.context.get("icd10"),
                            recommendation_edition=request.context.get("recommendation_edition"),
                            conditions={
                                "age_years": request.context.get("age"),
                                "age_group": {"Взрослые": "adult", "Дети": "child"}.get(
                                    request.context.get("age_group"), request.context.get("age_group")),
                            },
                        )
                    ],
                    purchases=[
                        dict(
                            item_id=i.item_id,
                            name=i.original.trade_name,
                            dosage=i.original.dosage,
                            quantity=str(i.original.quantity),
                            amount=str(i.original.amount),
                            currency=i.original.currency,
                        )
                        for i in request.items
                    ],
                    links=[dict(item_id=i.item_id, diagnosis_id=card.diagnosis_id) for i in request.items],
                )
            )
            book["Покупки"].reset_dimensions()
            rows = {row[0].value: index for index, row in enumerate(
                book["Покупки"].iter_rows(min_row=2, max_col=1), 2) if row[0].value is not None}
            return IntegrationWorkbook(value, rows)
        if set(book.sheetnames) != set(HEADERS):
            raise ValueError("Нужны только листы Заявка, Диагнозы, Покупки, Связи.")
        check_numeric_amounts(book["Покупки"], 10001)
        sheets = {}
        diagnosis_headers = HEADERS["Диагнозы"]
        purchase_rows = {}
        for name, base_headers in HEADERS.items():
            headers = list(base_headers)
            sheet = book[name]
            sheet.reset_dimensions()
            values = []
            for index, row in enumerate(sheet.iter_rows(), 1):
                if index > 10001 or len(row) > 100:
                    raise ValueError("Превышен предел строк или столбцов XLSX.")
                if any(c.data_type in {"f", "e"} for c in row):
                    raise ValueError("Используйте значения без формул и ошибок Excel.")
                fields = [cell.value for cell in row]
                if index == 1 and name == "Диагнозы":
                    while fields and fields[-1] is None:
                        fields.pop()
                    optional = fields[len(base_headers):]
                    if (fields[:len(base_headers)] != base_headers
                            or len(set(optional)) != len(optional)
                            or any(h not in DIAGNOSIS_OPTIONAL_HEADERS for h in optional)):
                        raise ValueError("Заголовки XLSX не совпадают с контрактом.")
                    headers = list(fields)
                    diagnosis_headers = headers
                if any(value is not None for value in fields[len(headers) :]):
                    raise ValueError("Лишние столбцы в XLSX.")
                fields = (fields + [None] * len(headers))[: len(headers)]
                if index == 1:
                    if fields != headers:
                        raise ValueError("Заголовки XLSX не совпадают с контрактом.")
                elif any(value is not None for value in fields):
                    values.append(fields)
                    if name == "Покупки":
                        purchase_rows[fields[0]] = index
            sheets[name] = values
        if len(sheets["Заявка"]) != 1 or sheets["Заявка"][0][0] != "ID заявки":
            raise ValueError("На листе Заявка должен быть только ID заявки.")
        value = dict(
            schema_version="integration-request.1",
            request_id=sheets["Заявка"][0][1],
            diagnoses=[dict(
                diagnosis_id=d[0], name=d[1], icd10=d[2],
                conditions={"age_years": _decimal(d[3]),
                            "age_group": dict(zip(diagnosis_headers, d)).get("Возрастная группа")},
                recommendation_edition=dict(zip(diagnosis_headers, d)).get("Редакция КР"),
            ) for d in sheets["Диагнозы"]],
            purchases=[
                dict(
                    item_id=p[0],
                    name=p[1],
                    dosage=p[2],
                    quantity=_decimal(p[3]),
                    amount=_decimal(p[4]),
                    currency=p[5],
                )
                for p in sheets["Покупки"]
            ],
            links=[dict(item_id=p[0], diagnosis_id=p[1]) for p in sheets["Связи"]],
        )
        try:
            return IntegrationWorkbook(IntegrationRequest.model_validate(value), purchase_rows)
        except ValidationError:
            raise ValueError("Некорректные ID, связи, диагнозы или поля покупок в XLSX.") from None
    finally:
        book.close()


def write_integration_report(result: IntegrationResult, output: Path):
    """HTML строится из сохранённого human_report без обращения к модели."""
    result = IntegrationResult.model_validate(result.model_dump())
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "result.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
    saved = IntegrationResult.model_validate_json((output / "result.json").read_bytes())
    (output / "report.html").write_text(render_human_report(saved.human_report), encoding="utf-8")
