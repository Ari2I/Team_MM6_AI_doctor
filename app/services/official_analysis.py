"""Сохранение поиска по официальным источникам в общей заявке."""

from collections import defaultdict
from uuid import uuid4

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models import Application, ApplicationStatus, ItemCheck
from app.services.official_reference import OfficialIndex, inn_key


def analyze_official(session: Session, application: Application, index: OfficialIndex) -> None:
    application = session.get(Application, application.id, with_for_update=True)
    if application is None:
        raise ValueError("Заявка отсутствует.")
    snapshot = index.snapshot
    application.demo_meta = (application.demo_meta or {}) | {
        "reference_profile": "official",
        "reference_version": snapshot.version,
        "corpus_version": snapshot.version,
        "official_coverage": snapshot.coverage,
        "official_as_of": snapshot.as_of.isoformat(),
        "approved_rule_count": len(snapshot.rules),
        "effective_mode": "offline",
        "fallback_reason": None,
        "model_attempted": False,
        "model_tokens": None,
        "model_summary": [],
        "model_review_priorities": [],
        "analysis_run_id": str(uuid4()),
    }
    details = {}
    item_ids = [item.id for item in application.items]
    if item_ids:
        session.execute(delete(ItemCheck).where(ItemCheck.item_id.in_(item_ids)))
    blocks = application.demo_meta.get("diagnosis_blocks")
    by_start = {blocks[str(diag.id)]: diag for diag in application.diagnoses} if blocks is not None else {}
    resolved = {item.id: index.resolve(item.drug_name or "", item.dosage) for item in application.items}
    item_blocks = application.demo_meta.get("item_blocks", {})
    purchases_by_block = defaultdict(set)
    for item in application.items:
        drug = resolved[item.id]
        if drug.mnn and blocks is not None:
            purchases_by_block[item_blocks[str(item.row_number)]].add(inn_key(drug.mnn))
    for item in application.items:
        drug = resolved[item.id]
        item.mnn = drug.mnn
        item.match_confidence = 1.0 if drug.mnn else None
        item.match_note = "official_lookup"
        details[str(item.id)] = index.details(drug)
        block = item_blocks[str(item.row_number)] if blocks is not None else None
        diagnoses = application.diagnoses if block is None else [by_start[block]]
        for diagnosis in diagnoses:
            evidence = index.lookup(drug, diagnosis.name, block_mnns=purchases_by_block[block])
            if block == 0:
                evidence |= {"reason_code": "OFFICIAL_DIAGNOSIS_MISSING",
                             "explanation": "Укажите диагноз перед этой покупкой."}
            category = evidence.pop("category", None)
            session.add(ItemCheck(
                item_id=item.id, diagnosis_id=diagnosis.id, reference_version=snapshot.version,
                category=category, needs_review=category is None, review_reason=evidence["reason_code"],
                source=next((citation["fragment_id"] for citation in evidence.get("citations", [])), None),
                comment=evidence["explanation"], evidence=evidence,
            ))
    application.demo_meta = application.demo_meta | {"official_item_details": details}
    application.requires_review = True  # Итоговая сверка специалистом сохраняется и при определённой категории.
    application.status = ApplicationStatus.ANALYZED
    session.flush()
