"""AI-книги: общий библиотечный парсер, локальные UUID и сохранённый вход."""

from __future__ import annotations

import sys
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

from openpyxl import load_workbook

from app.models import Application, ApplicationDiagnosis, ApplicationItem, ApplicationStatus
from app.services.excel_parser import ExcelParseError


class IntegrationUploadError(ValueError):
    """Только безопасное сообщение; не содержит ячеек, имени файла или исключения AI."""


def _is_integration_workbook(content: bytes) -> bool:
    with ZipFile(BytesIO(content)) as archive:
        if sum(info.file_size for info in archive.infolist()) > 100_000_000:
            raise ValueError("Превышен распакованный размер Excel.")
    book = load_workbook(BytesIO(content), read_only=True, data_only=False, keep_links=False)
    try:
        names = set(book.sheetnames)
        return {"Заявка", "Покупки"} <= names or bool({"Диагнозы", "Связи"} & names)
    finally:
        book.close()


def application_from_ai_workbook(
    content: bytes,
    *,
    filename: str,
    mode: str,
    diagnoses: list[str] | None = None,
    age_group: str | None = None,
) -> Application | None:
    """None означает прежний плоский Excel; llm_rag в template не импортируется."""
    try:
        structured = _is_integration_workbook(content)
    except Exception:
        raise ExcelParseError("Не удалось прочитать структуру Excel.") from None
    if not structured:
        return None
    if mode != "network":
        raise IntegrationUploadError("Для AI-книги нужен сетевой режим анализа.")
    if diagnoses:
        raise IntegrationUploadError("Диагнозы и связи AI-книги укажите только в самой книге.")
    try:
        source = Path(__file__).resolve().parents[2] / "ai" / "src"
        if source.is_dir() and str(source) not in sys.path:
            sys.path.insert(0, str(source))
        from llm_rag.integration_contracts import IntegrationRequest
        from llm_rag.integration_excel import read_integration_workbook

        loaded = read_integration_workbook(content)
        payload = loaded.request.model_dump(mode="json")
        if age_group:
            for diagnosis in payload["diagnoses"]:
                known = diagnosis["conditions"]["age_group"]
                if known is not None and known != age_group:
                    raise ValueError("Конфликт возрастной группы.")
                diagnosis["conditions"]["age_group"] = age_group
        payload = IntegrationRequest.model_validate(payload).model_dump(mode="json")
        application = Application(
            id=uuid4(),
            filename=filename,
            status=ApplicationStatus.PARSED,
        )
        diagnosis_ids = {row["diagnosis_id"]: uuid4() for row in payload["diagnoses"]}
        item_ids = {row["item_id"]: uuid4() for row in payload["purchases"]}
        application.diagnoses = [
            ApplicationDiagnosis(id=diagnosis_ids[row["diagnosis_id"]], name=row["name"])
            for row in payload["diagnoses"]
        ]
        application.items = [
            ApplicationItem(
                id=item_ids[row["item_id"]],
                row_number=loaded.rows_by_item_id[row["item_id"]],
                drug_name=row["name"],
                dosage=row["dosage"],
                diagnosis=None,
                quantity=Decimal(row["quantity"]) if row["quantity"] is not None else None,
                amount=Decimal(row["amount"]) if row["amount"] is not None else None,
            )
            for row in payload["purchases"]
        ]
        application.validation_issues = []
        for row in payload["diagnoses"]:
            row["diagnosis_id"] = str(diagnosis_ids[row["diagnosis_id"]])
        for row in payload["purchases"]:
            row["item_id"] = str(item_ids[row["item_id"]])
        for link in payload["links"]:
            link["item_id"] = str(item_ids[link["item_id"]])
            link["diagnosis_id"] = str(diagnosis_ids[link["diagnosis_id"]])
        payload["request_id"] = str(application.id)
        application.integration_input = payload
        groups = {row["conditions"]["age_group"] for row in payload["diagnoses"]}
        application.age_group = next(iter(groups)) if len(groups) == 1 else None
        return application
    except Exception:
        raise IntegrationUploadError(
            "Проверьте листы AI-книги, уникальность ID, явные связи, возраст, редакцию КР и числа."
        ) from None
