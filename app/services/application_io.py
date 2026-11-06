"""Импорт, анализ и восстановление результата демонстрационной заявки."""

from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app.models import Application, ApplicationDiagnosis, ApplicationItem, ApplicationStatus, ItemCheck
from app.models.application import ValidationIssue
from app.schemas.demo_result import ApplicationResult, CheckResult, DiagnosisSummary, ItemResult
from app.schemas.parsing import ParseResult
from app.schemas.validation import IssueCode, IssueLevel, ValidationReport
from app.services.analysis import AnalysisError, analyze_application
from app.services.rag_adapter import evidence_json, get_pipeline, live_results, verify_pair
from app.services.reference_seed import CATEGORIES, load_bundle
from app.services.summary import build_summary, build_warning_message, category_shares, compute_traffic_light
from app.services.text_utils import normalize_text

MAX_AMOUNT = Decimal("9999999999.99")
CENT = Decimal("0.01")
MAX_ITEMS = 2000
MAX_DIAGNOSES = 20


def application_diagnoses(parsed: ParseResult) -> list[str]:
    diagnoses = {}
    for item in parsed.items:
        for raw in (item.diagnosis or "").split(";"):
            if key := normalize_text(raw):
                diagnoses.setdefault(key, raw.strip())
    return list(diagnoses.values())


def validate_amounts(parsed: ParseResult, report: ValidationReport) -> None:
    from app.schemas.validation import ValidationIssue as Issue

    for item in parsed.items:
        if item.quantity is not None and not -1000 <= item.quantity.adjusted() <= 1000:
            item.quantity = None
        amount = item.amount
        if amount is None:
            continue
        if amount <= 0:
            item.amount = None
        elif not CENT <= amount <= MAX_AMOUNT or amount != round(amount, 2):
            report.issues.append(Issue(
                level=IssueLevel.WARNING, code=IssueCode.INVALID_NUMBER, row=item.row_number,
                column="amount", message="Сумма вне диапазона или указана с долями копейки. "
                "Строка исключена из расчёта долей.",
            ))
            item.amount = None


def verifier_for(application: Application, bundle=None):
    import os

    bundle = bundle or load_bundle()
    pipeline = get_pipeline(bundle.model_dump_json(), os.getenv("DEMO_RETRIEVAL", "lexical"))
    jobs = {}
    application.demo_meta = {
        "reference_version": bundle.reference_version,
        "corpus_version": bundle.corpus.corpus_version,
        "effective_mode": "offline",
        "analysis_run_id": str(uuid4()),
    }

    def verify(check, item, name, snapshot, drug_match):
        canonical = next((row.name for row in snapshot.diagnoses
                          if row.normalized_name == normalize_text(name)), name)
        if snapshot.fingerprint != bundle.content_digest:
            evidence = {
                "evidence_status": "UNCONFIRMED", "reason_code": "VERSION_MISMATCH",
                "explanation": "Активный справочник отличается от корпуса демо.",
                "effective_mode": "offline", "retrieval": pipeline.retrieval_method,
            }
        else:
            evidence, job = verify_pair(check, inn=item.mnn, diagnosis=canonical,
                                        bundle=bundle, pipeline=pipeline)
            if job is not None:
                jobs[(check.item_id, check.diagnosis_id)] = job
        if drug_match.candidates:
            evidence["match_candidates"] = drug_match.candidates
        return evidence

    return verify, jobs


def save_and_analyze(
    session: Session, filename: str, parsed: ParseResult, diagnoses: list[str],
    report: ValidationReport,
):
    bundle = load_bundle()
    application = Application(
        filename=filename, status=ApplicationStatus.PARSED, demo_meta={},
        diagnoses=[ApplicationDiagnosis(name=name) for name in diagnoses],
        items=[ApplicationItem(**item.model_dump()) for item in parsed.items],
        validation_issues=[ValidationIssue(**issue.model_dump()) for issue in report.issues],
    )
    session.add(application)
    session.flush()
    try:
        with session.begin_nested():
            verify, jobs = verifier_for(application, bundle)
            analyze_application(session, application.id, demo=True, verify=verify, commit=False)
    except (AnalysisError, SQLAlchemyError, ValueError, OSError):
        application.status = ApplicationStatus.ERROR
        application.requires_review = True
        return application.id, None, {}, bundle
    return application.id, application.demo_meta["analysis_run_id"], jobs, bundle


def enrich_saved_application(sessions, application_id: UUID, run_id: str, jobs: dict, bundle) -> None:
    results, global_reason = live_results(jobs, bundle)
    with sessions.begin() as session:
        application = session.get(Application, application_id, with_for_update=True)
        if application is None or (application.demo_meta or {}).get('analysis_run_id') != run_id:
            return
        checks = saved_checks(session, application_id)
        successful = 0
        reasons = []
        for check in checks:
            key = (check.item_id, check.diagnosis_id)
            if key not in jobs or check.evidence["evidence_status"] != "SUPPORTED":
                continue
            # Повторный анализ мог заменить проверки за время запроса к модели.
            if (check.source != jobs[key].fragment_id
                    or check.reference_version != bundle.reference_version):
                continue
            outcome = results.get(key, global_reason or "GIGACHAT_PROVIDER_ERROR")
            if isinstance(outcome, str):
                check.evidence = check.evidence | {"fallback_reason": outcome}
                reasons.append(outcome)
            else:
                verified = evidence_json(outcome, bundle, check.evidence["retrieval"])
                check.evidence = check.evidence | verified | {"effective_mode": "gigachat"}
                successful += 1
        mode = "mixed" if successful and reasons else "gigachat" if successful else "offline"
        application.demo_meta = application.demo_meta | {
            "effective_mode": mode, "fallback_reason": global_reason or next(iter(reasons), None),
        }


def saved_checks(session: Session, application_id: UUID) -> list[ItemCheck]:
    return list(session.scalars(
        select(ItemCheck).join(ApplicationItem).where(ApplicationItem.application_id == application_id)
    ))


def _summary(diagnosis, checks: list[CheckResult], items: list[ItemResult]) -> DiagnosisSummary:
    by_item = {check.item_id: check for check in checks}
    confirmed = {key: row for key, row in by_item.items() if row.evidence_status == "SUPPORTED"}
    latin = {category: key for key, category in CATEGORIES.items()}
    shares = category_shares([
        {"amount": item.amount, "category": latin.get(confirmed[item.item_id].category)
         if item.item_id in confirmed else None}
        for item in items
    ], exact=True)
    decision = compute_traffic_light(
        shares, False, demo=True,
        incomplete=len(confirmed) != len(items) or any(item.amount is None for item in items),
    )
    categories = CATEGORIES | {"undetermined": "undetermined"}
    return DiagnosisSummary(
        diagnosis_id=diagnosis.id, traffic_light=decision.color, reason=decision.reason,
        summary=build_summary(shares, decision.color, True),
        warning=build_warning_message([
            {"needs_review": True, "item_id": row.item_id, "review_reason": row.reason_code}
            for row in checks if row.item_id not in confirmed
        ]),
        amounts={label: shares[f"{key}_amount"].quantize(CENT) for key, label in categories.items()},
        shares={label: shares[key].quantize(CENT) for key, label in categories.items()},
        confirmed_count=len(confirmed), pair_count=len(checks),
    )


def load_result(session: Session, application_id: UUID) -> ApplicationResult | None:
    application = session.scalar(
        select(Application).where(Application.id == application_id).with_for_update(read=True).options(
            selectinload(Application.diagnoses), selectinload(Application.items),
            selectinload(Application.validation_issues),
        )
    )
    if application is None or not application.demo_meta or application.status != ApplicationStatus.ANALYZED:
        return None
    diagnoses = sorted(application.diagnoses, key=lambda row: (row.name.casefold(), row.id))
    raw_items = sorted(application.items, key=lambda row: (row.row_number, row.id))
    row_numbers = {item.id: item.row_number for item in raw_items}
    diag_order = {diag.id: index for index, diag in enumerate(diagnoses)}
    raw_checks = sorted(saved_checks(session, application_id),
                        key=lambda row: (row_numbers[row.item_id], diag_order[row.diagnosis_id]))
    checks = [CheckResult(
        item_id=row.item_id, diagnosis_id=row.diagnosis_id, category=CATEGORIES.get(row.category),
        fragment_id=row.source, reference_version=row.reference_version, needs_review=row.needs_review,
        **row.evidence,
    ) for row in raw_checks]
    candidates = {row.item_id: row.evidence["match_candidates"]
                  for row in raw_checks if row.evidence.get("match_candidates")}
    items = [ItemResult(
        item_id=row.id, row_number=row.row_number, drug_name=row.drug_name, diagnosis=row.diagnosis,
        dosage=row.dosage, quantity=row.quantity, amount=row.amount, mnn=row.mnn,
        original_cells=row.original_cells or {}, match_candidates=candidates.get(row.id, []),
    ) for row in raw_items]
    issues = sorted(application.validation_issues, key=lambda row: (row.row or 0, row.code, row.column or ""))
    return ApplicationResult(
        application_id=application.id, filename=application.filename, status=application.status,
        diagnoses=[{"diagnosis_id": row.id, "name": row.name} for row in diagnoses],
        items=items, checks=checks,
        issues=[{"level": row.level, "code": row.code, "row": row.row, "column": row.column,
                 "message": row.message} for row in issues],
        summary_by_diagnosis=[_summary(diag, [c for c in checks if c.diagnosis_id == diag.id], items)
                              for diag in diagnoses],
        known_total=sum((row.amount for row in items if row.amount is not None), Decimal("0")).quantize(CENT),
        requires_review=application.requires_review, **application.demo_meta,
    )
