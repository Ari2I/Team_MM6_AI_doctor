"""HTTP-оркестрация загрузки и просмотра заявок."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from time import monotonic
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import CATEGORY_LABELS, DISCLAIMER, get_validation_settings
from app.models import (
    Application,
    ApplicationDiagnosis,
    ApplicationItem,
    ApplicationStatus,
    ItemCheck,
    ValidationIssue,
)
from app.schemas.application_api import (
    APIErrors,
    APIIssue,
    ApplicationCard,
    ApplicationDetails,
    ApplicationListEntry,
    CheckDetails,
    ItemDetails,
)
from app.services.analysis import analyze_application
from app.services.excel_parser import ExcelParseError, parse_excel
from app.services.validator import validate_file, validate_parse_result

router = APIRouter(prefix="/applications", tags=["applications"])
logger = logging.getLogger(__name__)


def get_session() -> Iterator[Session]:
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _error(code: str, message: str) -> JSONResponse:
    body = APIErrors(errors=[APIIssue(level="critical", code=code, message=message)]).model_dump(
        mode="json"
    )
    return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content=body)


def _issues_response(issues: list[object]) -> JSONResponse:
    body = APIErrors(errors=[APIIssue.model_validate(issue) for issue in issues]).model_dump(
        mode="json"
    )
    return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content=body)


def _unique_diagnoses(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = value.strip()
        if clean and clean.casefold() not in seen:
            seen.add(clean.casefold())
            result.append(clean)
    return result


def _load_application(session: Session, application_id: UUID) -> Application | None:
    return session.scalar(
        select(Application)
        .options(
            selectinload(Application.diagnoses),
            selectinload(Application.items)
            .selectinload(ApplicationItem.item_checks)
            .selectinload(ItemCheck.diagnosis),
            selectinload(Application.validation_issues),
        )
        .where(Application.id == application_id)
    )


def _card(application: Application) -> ApplicationCard:
    items = []
    for item in sorted(application.items, key=lambda value: value.row_number):
        checks = [
            CheckDetails(
                diagnosis=check.diagnosis.name,
                category=check.category,
                category_label=CATEGORY_LABELS.get(check.category.value)
                if check.category
                else None,
                source=check.source,
                reference_version=check.reference_version,
                comment=check.comment,
                needs_review=check.needs_review,
                review_reason=check.review_reason,
            )
            for check in item.item_checks
        ]
        items.append(
            ItemDetails(
                row_number=item.row_number,
                drug_name=item.drug_name,
                mnn=item.mnn,
                match_confidence=item.match_confidence,
                amount=item.amount,
                checks=checks,
            )
        )
    return ApplicationCard(
        application=ApplicationDetails.model_validate(application),
        disclaimer=DISCLAIMER,
        diagnoses=[diagnosis.name for diagnosis in application.diagnoses],
        items=items,
        issues=[APIIssue.model_validate(issue) for issue in application.validation_issues],
    )


@router.post(
    "",
    response_model=ApplicationCard,
    status_code=status.HTTP_201_CREATED,
    summary="Загрузить и проанализировать заявку",
    description="Принять XLSX-заявку, проверить её и вернуть сохранённую карточку анализа.",
    responses={422: {"model": APIErrors, "description": "Ошибки файла или полей заявки"}},
)
def create_application(
    file: Annotated[UploadFile, File(description="Файл заявки XLSX")],
    session: Annotated[Session, Depends(get_session)],
    diagnoses: Annotated[
        list[str] | None,
        Form(
            description="Диагнозы; повторите поле для каждого значения",
            examples=[["Учебный диагноз 01"]],
        ),
    ] = None,
) -> ApplicationCard | JSONResponse:
    started = monotonic()
    settings = get_validation_settings()
    file_report = validate_file(file.filename or "", file.size or 0)
    if not file_report.is_valid:
        file.file.close()
        return _issues_response(file_report.issues)
    try:
        content = file.file.read(settings.max_excel_size_bytes + 1)
        if len(content) > settings.max_excel_size_bytes:
            return _error("file_too_large", "Размер файла превышает допустимый лимит.")
        parsed = parse_excel(content)
    except ExcelParseError:
        return _error(
            "invalid_excel", "Не удалось прочитать файл Excel. Передайте исправный файл XLSX."
        )
    finally:
        file.file.close()

    diagnoses = diagnoses or []
    report = validate_parse_result(parsed, diagnoses)
    if not report.is_valid:
        return _issues_response(report.issues)

    application = Application(
        filename=file.filename or "application.xlsx", status=ApplicationStatus.PARSED
    )
    application.diagnoses = [
        ApplicationDiagnosis(name=name)
        for name in _unique_diagnoses(
            [*(item.diagnosis for item in parsed.items if item.diagnosis), *diagnoses]
        )
    ]
    canonical_diagnoses = {row.name.casefold(): row.name for row in application.diagnoses}
    application.items = [
        ApplicationItem(
            row_number=item.row_number,
            drug_name=item.drug_name,
            diagnosis=canonical_diagnoses[item.diagnosis.strip().casefold()]
            if item.diagnosis
            else None,
            dosage=item.dosage,
            quantity=item.quantity,
            amount=item.amount,
        )
        for item in parsed.items
    ]
    application.validation_issues = [
        ValidationIssue(
            level=issue.level,
            code=issue.code.value,
            message=issue.message,
            row=issue.row,
            column=issue.column,
        )
        for issue in report.issues
    ]
    session.add(application)
    session.commit()
    application = analyze_application(session, application.id)
    logger.info(
        "application_id=%s status=%s processing_seconds=%.3f",
        application.id,
        application.status.value,
        monotonic() - started,
    )
    session.expire_all()
    loaded = _load_application(session, application.id)
    assert loaded is not None
    return _card(loaded)


@router.get(
    "",
    response_model=list[ApplicationListEntry],
    summary="Получить список заявок",
    description="Заявки по убыванию времени создания с пагинацией limit/offset.",
    responses={422: {"model": APIErrors, "description": "Недопустимые параметры страницы"}},
)
def list_applications(
    session: Annotated[Session, Depends(get_session)],
    limit: int = Query(20, ge=1, le=100, description="Размер страницы", examples=[20]),
    offset: int = Query(0, ge=0, description="Смещение", examples=[0]),
) -> list[ApplicationListEntry]:
    applications = session.scalars(
        select(Application)
        .order_by(Application.created_at.desc(), Application.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return [ApplicationListEntry.model_validate(application) for application in applications]


@router.get(
    "/{application_id}",
    response_model=ApplicationCard,
    summary="Получить карточку заявки",
    description="Сохранённая карточка со строками, проверками пар и замечаниями валидатора.",
    responses={
        404: {"model": APIErrors, "description": "Заявка не найдена"},
        422: {"model": APIErrors, "description": "Недопустимый идентификатор"},
    },
)
def get_application(
    application_id: UUID, session: Annotated[Session, Depends(get_session)]
) -> ApplicationCard | JSONResponse:
    application = _load_application(session, application_id)
    if application is None:
        return JSONResponse(
            status_code=404,
            content={
                "errors": [
                    {
                        "level": "error",
                        "code": "not_found",
                        "message": "Заявка не найдена.",
                        "row": None,
                        "column": None,
                    }
                ]
            },
        )
    return _card(application)
