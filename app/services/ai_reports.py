"""Запись проверенного IntegrationResult и чтение снимка без AI-библиотеки/сети."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Application, ApplicationAIResult
from app.models.enums import ReviewReason
from app.schemas.ai_report import AIReport, AIReportCheck
from app.schemas.analysis import PairCheck, ReferenceSnapshot
from app.services.integration_mapping import IntegrationMappingError, prepare_integration_request
from app.services.matching import match_diagnosis, match_drug
from app.services.rules import check_pair


class AIReportError(ValueError):
    def __init__(self) -> None:
        super().__init__("Не удалось сохранить или прочитать ИИ-отчёт.")


def _validated_result(value: dict[str, Any]) -> dict[str, Any]:
    try:
        from llm_rag.integration_contracts import IntegrationResult

        return IntegrationResult.model_validate(value).model_dump(mode="json")
    except (ImportError, ValidationError, ValueError, TypeError):
        raise AIReportError() from None


def _same_request(application: Application, result: dict[str, Any], currency: str) -> None:
    """Не принять результат другой/изменённой заявки, даже если request_id совпал."""
    from llm_rag.integration_contracts import IntegrationRequest

    from app.services.integration_mapping import validate_integration_request

    try:
        expected = IntegrationRequest.model_validate(
            validate_integration_request(
                prepare_integration_request(application, currency=currency)
            )
        )
        actual = IntegrationRequest.model_validate(result["request"])
    except (IntegrationMappingError, ValidationError, ValueError, KeyError, TypeError):
        raise AIReportError() from None
    if actual.request_id != expected.request_id:
        raise AIReportError()
    if actual.versions != expected.versions:
        raise AIReportError()
    # Decimal сравнивается по значению: PostgreSQL Numeric(12, 2) может вернуть
    # другую шкалу, не меняя исходной суммы. Тексты и остальные поля — строго.
    for field, key in (("purchases", "item_id"), ("diagnoses", "diagnosis_id")):
        if {getattr(row, key): row for row in getattr(actual, field)} != {
            getattr(row, key): row for row in getattr(expected, field)
        }:
            raise AIReportError()
    if {(r.item_id, r.diagnosis_id) for r in actual.links} != {
        (r.item_id, r.diagnosis_id) for r in expected.links
    }:
        raise AIReportError()


def disabled_backend_check() -> PairCheck:
    """Отсутствие SQL-проверки явно отражается в сохранённом AI-отчёте."""
    return PairCheck(
        source="Проверка по справочнику backend отключена.",
        needs_review=True,
        review_reason=ReviewReason.AI_REVIEW_REQUIRED,
    )


def result_checks(
    application: Application,
    result: dict[str, Any],
    snapshot: ReferenceSnapshot | None,
) -> list[AIReportCheck]:
    """Проекция уже валидного результата. Порядок строк закреплён human-report.1."""
    items = {str(row.id): row for row in application.items}
    diagnoses = {str(row.id): row for row in application.diagnoses}
    checks = []
    pairs = [(pair, True) for pair in result["pairs"]]
    pairs += [(pair, False) for pair in result["unprocessed_pairs"]]
    for number, (pair, processed) in enumerate(pairs, 1):
        item = items[pair["item_id"]]
        diagnosis = diagnoses[pair["diagnosis_id"]]
        local = (
            check_pair(
                match_drug(item.drug_name or "", snapshot),
                match_diagnosis(diagnosis.name, snapshot),
                snapshot,
            )
            if snapshot is not None
            else disabled_backend_check()
        )
        decision = pair["decision"] if processed else {}
        rule = decision.get("upstream_rule_category")
        proposal = decision.get("proposed_category")
        basis = decision.get("classification_basis")
        conflict = basis == "CONFLICT" or bool(
            local.category and any(value and value != local.category for value in (rule, proposal))
        )
        checks.append(
            AIReportCheck(
                number=number,
                item_id=item.id,
                diagnosis_id=diagnosis.id,
                row_number=item.row_number,
                backend_rule=local,
                ai_rule_category=rule,
                ai_proposed_category=proposal,
                classification_basis=basis,
                conflict=conflict,
                processed=processed,
                generation_mode=pair["generation_mode"] if processed else None,
                form_status=pair["form_status"] if processed else None,
                reasons=pair["reasons"] if processed else [pair["reason"]],
            )
        )
    return checks


def save_ai_result(
    session: Session,
    application_id: UUID,
    result: dict[str, Any],
    *,
    currency: str,
    backend_revision: str,
    ai_revision: str,
    snapshot: ReferenceSnapshot | None = None,
    use_backend_reference: bool = True,
) -> ApplicationAIResult:
    """Без commit: запись и изменения заявки фиксируются одной транзакцией.

    Вызов только после результата worker. Локальная валидация не запускает фасад.
    Известная age_group сверяется с сохранённой заявкой вместе с диагнозами.
    """
    from app.services.analysis import _load_snapshot

    if any(
        not isinstance(revision, str) or re.fullmatch(r"[0-9a-f]{40}", revision) is None
        for revision in (backend_revision, ai_revision)
    ):
        raise AIReportError()
    value = _validated_result(result)
    application = session.scalar(
        select(Application)
        .where(Application.id == application_id)
        .options(selectinload(Application.items), selectinload(Application.diagnoses))
        .with_for_update(of=Application)
        .execution_options(populate_existing=True)
    )
    if application is None:
        raise AIReportError()
    _same_request(application, value, currency)
    now = datetime.now(UTC)
    try:
        reference = (snapshot or _load_snapshot(session)) if use_backend_reference else None
        checks = result_checks(application, value, reference)
        report = AIReport(
            application_id=application_id,
            saved_at=now,
            backend_revision=backend_revision,
            ai_revision=ai_revision,
            completeness=value["completeness"],
            human_report=value["human_report"],
            checks=checks,
        )
    except (ValidationError, ValueError, TypeError, KeyError):
        raise AIReportError() from None
    stored = session.get(ApplicationAIResult, application_id)
    if stored is None:
        stored = ApplicationAIResult(application_id=application_id)
        session.add(stored)
    stored.result = value
    stored.checks = [check.model_dump(mode="json") for check in report.checks]
    stored.backend_revision = backend_revision
    stored.ai_revision = ai_revision
    stored.saved_at = now
    session.flush()
    return stored


def read_ai_report(stored: ApplicationAIResult) -> AIReport:
    """Только чтение backend-схемой; не импортирует llm_rag и SDK."""
    try:
        if stored.result["schema_version"] != "integration-result.1":
            raise AIReportError()
        if stored.result["request"]["request_id"] != str(stored.application_id):
            raise AIReportError()
        return AIReport(
            application_id=stored.application_id,
            saved_at=stored.saved_at,
            backend_revision=stored.backend_revision,
            ai_revision=stored.ai_revision,
            completeness=stored.result["completeness"],
            human_report=stored.result["human_report"],
            checks=stored.checks,
        )
    except (ValidationError, KeyError, TypeError, ValueError):
        raise AIReportError() from None
