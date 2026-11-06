"""Сохранение поиска по официальным источникам в общей заявке."""

from uuid import uuid4

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models import Application, ApplicationStatus, ItemCheck
from app.services.official_reference import OfficialIndex


def analyze_official(session: Session, application: Application, index: OfficialIndex) -> None:
    application = session.get(Application, application.id, with_for_update=True)
    if application is None:
        raise ValueError("Заявка отсутствует.")
    snapshot = index.snapshot
    application.demo_meta = {
        "reference_profile": "official",
        "reference_version": snapshot.version,
        "corpus_version": snapshot.version,
        "official_coverage": snapshot.coverage,
        "official_as_of": snapshot.as_of.isoformat(),
        "effective_mode": "offline",
        "analysis_run_id": str(uuid4()),
    }
    details = {}
    item_ids = [item.id for item in application.items]
    if item_ids:
        session.execute(delete(ItemCheck).where(ItemCheck.item_id.in_(item_ids)))
    for item in application.items:
        drug = index.resolve(item.drug_name or "")
        item.mnn = drug.mnn
        item.match_confidence = 1.0 if drug.mnn else None
        item.match_note = "official_lookup"
        details[str(item.id)] = index.details(drug)
        for diagnosis in application.diagnoses:
            evidence = index.lookup(drug, diagnosis.name)
            session.add(ItemCheck(
                item_id=item.id, diagnosis_id=diagnosis.id, reference_version=snapshot.version,
                needs_review=True, review_reason=evidence["reason_code"],
                comment=evidence["explanation"], evidence=evidence,
            ))
    application.demo_meta = application.demo_meta | {"official_item_details": details}
    application.requires_review = True
    application.status = ApplicationStatus.ANALYZED
    session.flush()
