"""Оркестрация детерминированного анализа заявки и сохранение результата."""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import AISettings
from app.models import (
    Application,
    ApplicationAIResult,
    ApplicationDiagnosis,
    ApplicationStatus,
    ItemCheck,
    ReferenceVersion,
)
from app.schemas.analysis import ReferenceDiagnosis, ReferenceDrug, ReferenceRule, ReferenceSnapshot
from app.services.ai_adapter import AIAdapter, AIInput
from app.services.ai_history import archive_ai_result
from app.services.application_ai import (
    analyze_network_application,
    project_ai_pair,
    project_pair,
    resolved_inn,
)
from app.services.integration_mapping import diagnoses_for_item
from app.services.matching import match_diagnosis, match_drug
from app.services.rules import check_pair, detect_conflicts
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
        return ReferenceSnapshot(reference_version="unconfigured")
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


def _set_error(
    session: Session, application: Application, *, clear_checks: bool = False
) -> Application:
    application.analysis_run_id = uuid4()
    archive_ai_result(session, session.get(ApplicationAIResult, application.id))
    session.execute(
        delete(ApplicationAIResult).where(ApplicationAIResult.application_id == application.id)
    )
    if clear_checks:
        items = list(application.items)
        if items:
            session.execute(delete(ItemCheck).where(ItemCheck.item_id.in_([i.id for i in items])))
        for item in items:
            item.mnn = None
            item.match_confidence = None
            item.match_note = None
        application.category_shares = None
        application.traffic_light = None
        application.summary = None
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
    settings: AISettings | None = None
    previous_run_id: UUID | None = None
    lock_acquired = False
    try:
        settings = AISettings()
        ai_adapter = AIAdapter() if settings.use_backend_reference else None
        if ai_adapter is not None:
            settings = ai_adapter.settings
        # Как в save_ai_result: сначала заявка, затем её снимок. Блокировка
        # удерживается до общего commit карточки и AI-отчёта.
        locked = session.execute(
            select(Application)
            .where(Application.id == application.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if locked is not None:
            application = locked.scalar_one()
        previous_run_id = application.analysis_run_id
        lock_acquired = True
        application.analysis_run_id = uuid4()
        snapshot = _load_snapshot(session) if settings.use_backend_reference else None
        items = list(application.items)
        archive_ai_result(session, session.get(ApplicationAIResult, application.id))
        # Повторный анализ не должен показывать прежний отчёт как новый результат,
        # включая template/offline и отказ сетевого worker.
        session.execute(
            delete(ApplicationAIResult).where(ApplicationAIResult.application_id == application.id)
        )
        if items:
            session.execute(
                delete(ItemCheck).where(ItemCheck.item_id.in_([item.id for item in items]))
            )
        session.flush()

        network = None
        if settings.mode == "network":
            for item in items:
                if snapshot is not None:
                    matched = match_drug(item.drug_name or "", snapshot)
                    item.mnn = matched.mnn
                    item.match_confidence = matched.confidence
                    item.match_note = matched.status
                else:
                    # Прежнее сопоставление (в том числе demo-api-2) не должно
                    # стать входом нового фасада при повторном анализе.
                    item.mnn = None
                    item.match_confidence = None
                    item.match_note = None
            session.flush()
            network = analyze_network_application(session, application, settings, snapshot)

        all_checks: list[ItemCheck] = []
        item_best: list[dict[str, object]] = []
        for item in items:
            drug_match = match_drug(item.drug_name or "", snapshot) if snapshot else None
            if drug_match is not None:
                item.mnn = drug_match.mnn
                item.match_confidence = drug_match.confidence
                item.match_note = drug_match.status
            if network is not None and not item.mnn:
                if inn := resolved_inn(network, str(item.id)):
                    item.mnn = inn
                    item.match_note = "matched_grls"
                    # Фасад возвращает МНН, но числовой confidence не предоставляет.
                    item.match_confidence = None
            checks_for_item: list[tuple[ApplicationDiagnosis, object, object]] = []
            for diagnosis_row in diagnoses_for_item(application, item):
                diagnosis_name = diagnosis_row.name
                if snapshot is None:
                    assert network is not None
                    pair_check = project_ai_pair(network, str(item.id), str(diagnosis_row.id))
                else:
                    assert drug_match is not None
                    diagnosis_match = match_diagnosis(diagnosis_name, snapshot)
                    pair_check = check_pair(drug_match, diagnosis_match, snapshot)
                if snapshot is not None and network is not None:
                    pair_check = project_pair(
                        pair_check, network, str(item.id), str(diagnosis_row.id)
                    )
                elif network is None:
                    assert ai_adapter is not None and drug_match is not None
                    pair_check = ai_adapter.analyze(
                        AIInput(
                            drug_name=item.drug_name or "",
                            mnn=drug_match.mnn,
                            diagnosis=diagnosis_name,
                            dosage=item.dosage,
                            quantity=item.quantity,
                            amount=item.amount,
                            age_group=application.age_group,
                        ),
                        pair_check,
                    )
                if ai_adapter is not None and ai_adapter.last_mnn and not drug_match.mnn:
                    item.mnn = ai_adapter.last_mnn
                    item.match_note = "matched_grls"
                    item.match_confidence = 1.0
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
        if (
            snapshot is not None
            and snapshot.reference_version == "unconfigured"
            and not settings.registry_path
            and network is None
        ):
            application.warning_message = (
                "Рабочий справочник не подключён. МНН и категории не определены; "
                "требуется проверка человеком."
            )
        application.summary = build_summary(shares, decision.color, has_review)
        if not settings.use_backend_reference and network is not None and network.result:
            human_report = network.result["human_report"]
            application.summary = " ".join(
                [human_report["summary"], human_report["recommendation_note"], application.summary]
            )
        application.status = ApplicationStatus.ANALYZED
        session.commit()
        return application
    except Exception:
        session.rollback()
        application = session.get(Application, application_id)
        if application is None:
            raise AnalysisError("Заявка не найдена.") from None
        locked = session.execute(
            select(Application)
            .where(Application.id == application.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if locked is not None:
            application = locked.scalar_one()
        if lock_acquired and application.analysis_run_id != previous_run_id:
            # После rollback другой запуск успел завершиться. Не очищать его
            # актуальные проверки и отчёт ошибкой более старого анализа.
            session.commit()
            return application
        return _set_error(
            session,
            application,
            clear_checks=settings is not None and not settings.use_backend_reference,
        )
