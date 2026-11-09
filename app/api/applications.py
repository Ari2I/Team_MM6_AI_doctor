"""Демонстрационный HTTP API заявок и выгрузок."""

from hashlib import sha256
from io import BytesIO
import json
import logging
from xml.etree.ElementTree import ParseError
from pathlib import PurePath
from typing import Annotated, Literal
from uuid import UUID
from zipfile import BadZipFile, ZipFile

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_validation_settings
from app.models.reference import ReferenceVersion
from app.schemas.demo_result import ApplicationResult
from app.services.analysis import AnalysisError
from app.services.application_io import (
    MAX_DIAGNOSES, MAX_ITEMS, application_diagnoses, enrich_saved_application,
    load_result, save_and_analyze, validate_amounts,
)
from app.services.excel_parser import ExcelLayoutChoice, ExcelParseError, parse_excel
from app.services.export import build_html, build_xlsx
from app.services.reference_seed import DEMO_FILE, load_bundle
from app.services.official_explanation import enrich_official, gigachat_settings
from app.services.official_reference import load_official, official_path
from app.services.validator import validate_file, validate_parse_result
from app.services.workbook_limits import WorkbookTooLarge, check_sheet_bounds

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)
DEMO_SHA256 = "4c53c2f1902625fe617425b637aa9c4c83fbe591f9d3b990ab8740b987f51f1a"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_EXPANDED_SIZE = 50 * 1024 * 1024


def sessions():
    from app.db.session import SessionLocal
    return SessionLocal


def issue(code: str, message: str) -> dict:
    return {"level": "critical", "code": code, "message": message, "row": None, "column": None}


def reject(status: int, *issues: dict):
    raise HTTPException(status_code=status, detail={"issues": issues})


def read_application(application_id: UUID) -> ApplicationResult:
    try:
        with sessions()() as session:
            result = load_result(session, application_id)
    except SQLAlchemyError:
        reject(503, issue("storage_unavailable", "Хранилище заявок недоступно."))
    if result is None:
        reject(404, issue("application_not_found", "Завершённая заявка не найдена."))
    return result


@router.get("/ready")
def readiness():
    try:
        path = official_path()
        bundle = load_bundle() if path is None else None
        official = load_official(path).snapshot if path is not None else None
        with sessions()() as session:
            if bundle is not None:
                active = session.scalars(select(ReferenceVersion).where(ReferenceVersion.is_active)).all()
                if len(active) != 1 or active[0].fingerprint != bundle.content_digest:
                    reject(503, issue("reference_unavailable", "Активируйте справочник демо."))
            # Проверяется наличие таблиц результата и новых колонок миграции.
            from app.models.application import Application, ItemCheck
            session.execute(select(Application.demo_meta).limit(0))
            session.execute(select(ItemCheck.evidence).limit(0))
    except (SQLAlchemyError, ValueError, OSError):
        reject(503, issue("storage_unavailable", "Проверьте БД, миграции и справочник."))
    if official is None:
        return {"status": "ready", "reference_version": bundle.reference_version}
    config = gigachat_settings()
    return {"status": "ready", "reference_version": official.version,
            "gigachat_b2b_configured": bool(config["GIGACHAT_CREDENTIALS"]) != bool(config["GIGACHAT_ACCESS_TOKEN"]),
            "approved_rules": len(official.rules)}


@router.get("/references")
def reference_availability():
    path = official_path()
    if path is None:
        return {"official": None, "default": "demo"}
    try:
        snapshot = load_official(path).snapshot
    except (ValueError, OSError):
        return {"official": None, "default": "demo", "official_error": "Справочник недоступен."}
    return {
        "official": {"version": snapshot.version, "coverage": snapshot.coverage,
                     "as_of": snapshot.as_of, "registrations": len(snapshot.registrations),
                     "evidence": len(snapshot.evidence), "compositions": len(snapshot.compositions),
                     "diagnoses": len(snapshot.diagnoses),
                     "documents": len({row.source.url for row in snapshot.documents}),
                     "indexed_documents": len({page.url for page in snapshot.pages}),
                     "original_documents": len({page.url for page in snapshot.pages if not page.copy_url}),
                     "published_copies": len({page.url for page in snapshot.pages if page.copy_url}),
                     "rules": len(snapshot.rules)},
        "default": "official",
    }


@router.get("/demo.xlsx")
def demo_file():
    return FileResponse(DEMO_FILE, media_type=XLSX, filename="demo.xlsx")


@router.post("/applications", response_model=ApplicationResult, status_code=201)
def create_application(
    file: Annotated[UploadFile, File()],
    mode: Annotated[Literal["offline", "gigachat"] | None, Form()] = None,
    reference: Annotated[Literal["demo", "official"] | None, Form()] = None,
    sheet: Annotated[str | None, Form()] = None,
    columns: Annotated[str | None, Form()] = None,
):
    limit = get_validation_settings().max_excel_size_bytes
    filename = PurePath((file.filename or "").replace("\\", "/")).name.replace("\x00", "")[:255]
    content = file.file.read(limit + 1)
    report = validate_file(filename, max(file.size or 0, len(content)))
    if not report.is_valid:
        reject(413 if any(i.code == "file_too_large" for i in report.issues) else 422,
               *(i.model_dump(mode="json") for i in report.issues))
    path = official_path()
    use_official = reference == "official" or (reference is None and path is not None)
    if not use_official and mode == "gigachat" and sha256(content).hexdigest() != DEMO_SHA256:
        reject(422, issue("unapproved_demo_file", "Для GigaChat загрузите исходный файл «Демо Excel»."))
    try:
        selected_columns = json.loads(columns) if columns else {}
        if not isinstance(selected_columns, dict) or any(type(value) is not int for value in selected_columns.values()):
            raise ValueError()
    except ValueError:
        reject(422, issue("invalid_layout", "Сопоставление столбцов недействительно."))
    try:
        with ZipFile(BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > 1000 or sum(entry.file_size for entry in entries) > MAX_EXPANDED_SIZE:
                reject(413, issue("workbook_too_large", "Внутренний объём книги превышает лимит."))
            check_sheet_bounds(archive)
        parsed = parse_excel(content, demo=True, diagnosis_blocks=use_official,
                             sheet=sheet if use_official else None, column_choices=selected_columns)
    except ExcelLayoutChoice as error:
        raise HTTPException(409, detail={"layout": error.options}) from None
    except WorkbookTooLarge:
        reject(413, issue("workbook_too_large", "Координаты ячеек превышают размеры шаблона демо."))
    except (BadZipFile, ParseError, KeyError, RuntimeError, ValueError):
        reject(422, issue("invalid_xlsx", "Не удалось открыть файл Excel."))
    except ExcelParseError as error:
        reject(422, issue("invalid_xlsx", str(error)))
    diagnoses = list(parsed.diagnosis_blocks.values()) if use_official else application_diagnoses(parsed)
    if len(parsed.items) > MAX_ITEMS or len(diagnoses) > MAX_DIAGNOSES:
        reject(413, issue("application_too_large",
                          f"Допустимо до {MAX_ITEMS} покупок и {MAX_DIAGNOSES} диагнозов."))
    report = validate_parse_result(parsed, request_diagnoses=diagnoses, demo=True, blocked=use_official)
    if not report.is_valid:
        reject(422, *(i.model_dump(mode="json") for i in report.issues))
    validate_amounts(parsed, report)
    official = None
    if use_official:
        try:
            if path is None:
                raise FileNotFoundError
            official = load_official(path)
        except (ValueError, OSError):
            reject(503, issue("official_reference_unavailable", "Загрузите проверенный снимок официального справочника."))
    try:
        factory = sessions()
        with factory.begin() as session:
            application_id, run_id, jobs, bundle = save_and_analyze(
                session, filename, parsed, diagnoses, report, official=official,
            )
    except (SQLAlchemyError, AnalysisError, ValueError, OSError) as error:
        logger.warning("Анализ не завершён (%s).", type(error).__name__)
        reject(503, issue("analysis_unavailable", "Анализ недоступен. Проверьте БД и справочник демо."))
    if run_id is None:
        raise HTTPException(status_code=503, detail={
            'application_id': str(application_id),
            'issues': [issue('analysis_unavailable', 'Анализ недоступен. Проверьте справочник демо.')],
        })
    if (use_official and mode != "offline") or mode == "gigachat":
        try:
            if use_official:
                enrich_official(factory, application_id, run_id, official, enable_ml2=mode == "gigachat")
            else:
                enrich_saved_application(factory, application_id, run_id, jobs, bundle)
        except SQLAlchemyError:
            logger.warning("Обогащение результата не записано, локальная проверка сохранена.")
    return read_application(application_id)


@router.get("/applications/{application_id}", response_model=ApplicationResult)
def get_application(application_id: UUID):
    return read_application(application_id)


@router.get("/applications/{application_id}/result.xlsx")
def result_xlsx(application_id: UUID):
    return Response(build_xlsx(read_application(application_id)), media_type=XLSX,
                    headers={"Content-Disposition": 'attachment; filename="result.xlsx"'})


@router.get("/applications/{application_id}/report.html")
def report_html(application_id: UUID):
    return Response(build_html(read_application(application_id)), media_type="text/html",
                    headers={"Content-Disposition": 'attachment; filename="report.html"',
                             "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})
