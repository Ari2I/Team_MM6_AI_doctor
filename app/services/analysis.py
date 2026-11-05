"""Оркестрация детерминированного анализа заявки и сохранение результата."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import (
    Application,
    ApplicationDiagnosis,
    ApplicationStatus,
    ItemCheck,
    ReferenceVersion,
)
from app.schemas.analysis import ReferenceDiagnosis, ReferenceDrug, ReferenceRule, ReferenceSnapshot
from app.services.matching import match_diagnosis, match_drug
from app.services.rules import check_pair, detect_conflicts, pairs_for_item
from app.services.summary import (
    best_category,
    build_summary,
    build_warning_message,
    category_shares,
    compute_traffic_light,
)


class AnalysisError(RuntimeError):
    """Ошибка, которую можно безопасно отобразить пользователю."""


def _load_snapshot(session: Session) -> ReferenceSnapshot:
    version = session.scalar(
        select(ReferenceVersion)
        .where(ReferenceVersion.is_active.is_(True))
        .order_by(ReferenceVersion.version.desc())
    )
    if version is None:
        raise AnalysisError("Активная версия справочника не найдена.")
    return ReferenceSnapshot(
        reference_version=version.version,
        drugs=[ReferenceDrug(trade_name=row.trade_name, mnn=row.mnn) for row in version.drugs],
        diagnoses=[
            ReferenceDiagnosis(diagnosis_id=row.diagnosis_id, name=row.name)
            for row in version.diagnoses
        ],
        rules=[
            ReferenceRule(
                mnn=row.mnn, diagnosis_id=row.diagnosis_id, category=row.category, source=row.source
            )
            for row in version.rules
        ],
    )


def _set_error(session: Session, application: Application) -> Application:
    application.status = ApplicationStatus.ERROR
    application.requires_review = True
    application.warning_message = "Анализ не завершён: требуется проверка человеком."
    session.commit()
    return application


def analyze_application(session: Session, application_id: UUID | str) -> Application:
    """Полностью пересчитать заявку; повторный запуск заменяет старые pair-checks."""
    application = session.get(Application, application_id)
    if application is None:
        raise AnalysisError("Заявка не найдена.")
    try:
        snapshot = _load_snapshot(session)
        diagnoses = list(application.diagnoses)
        diagnosis_names = [diagnosis.name for diagnosis in diagnoses]
        items = list(application.items)
        if items:
            session.execute(
                delete(ItemCheck).where(ItemCheck.item_id.in_([item.id for item in items]))
            )
        session.flush()

        all_checks: list[ItemCheck] = []
        item_best: list[dict[str, object]] = []
        for item in items:
            drug_match = match_drug(item.drug_name or "", snapshot)
            item.mnn = drug_match.mnn
            item.match_confidence = drug_match.confidence
            item.match_note = drug_match.status
            pair_names = pairs_for_item(item.diagnosis, diagnosis_names)
            checks_for_item: list[tuple[ApplicationDiagnosis, object, object]] = []
            for diagnosis_name in pair_names:
                diagnosis_row = next((row for row in diagnoses if row.name == diagnosis_name), None)
                if diagnosis_row is None:
                    continue
                diagnosis_match = match_diagnosis(diagnosis_name, snapshot)
                pair_check = check_pair(drug_match, diagnosis_match, snapshot)
                db_check = ItemCheck(
                    item_id=item.id,
                    diagnosis_id=diagnosis_row.id,
                    category=pair_check.category,
                    source=pair_check.source,
                    reference_version=pair_check.reference_version,
                    comment=pair_check.comment,
                    needs_review=pair_check.needs_review,
                    review_reason=pair_check.review_reason.value
                    if pair_check.review_reason
                    else None,
                )
                checks_for_item.append((diagnosis_row, db_check, pair_check))
            detected = detect_conflicts([check for _, _, check in checks_for_item])
            for (_, db_check, _), pair_check in zip(checks_for_item, detected, strict=False):
                db_check.needs_review = pair_check.needs_review
                db_check.review_reason = (
                    pair_check.review_reason.value if pair_check.review_reason else None
                )
                db_check.comment = pair_check.comment
                session.add(db_check)
                all_checks.append(db_check)
            item_best.append({"amount": item.amount, "best_category": best_category(detected)})

        session.flush()
        shares = category_shares(item_best)
        has_review = any(check.needs_review for check in all_checks)
        decision = compute_traffic_light(shares, has_review)
        application.traffic_light = decision.color
        application.category_shares = shares
        application.requires_review = has_review
        application.warning_message = build_warning_message(all_checks)
        application.summary = build_summary(shares, decision.color, has_review)
        application.status = ApplicationStatus.ANALYZED
        session.commit()
        return application
    except Exception:
        session.rollback()
        application = session.get(Application, application_id)
        if application is None:
            raise AnalysisError("Заявка не найдена.") from None
        return _set_error(session, application)
