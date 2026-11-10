"""Передача проверок ML-2 в парный интерфейс llm_rag.Pipeline.process_pair."""

from collections.abc import Iterator

from app.schemas.demo_result import ApplicationResult
from llm_rag.contracts import DosageCheck, PairContext, PairInput


def rag_pairs(result: ApplicationResult) -> Iterator[tuple[PairContext, PairInput]]:
    items = {row.item_id: row for row in result.items}
    diagnoses = {row.diagnosis_id: row.name for row in result.diagnoses}
    for check in result.checks:
        item = items[check.item_id]
        ml2 = check.ml2_classification
        proposed = (
            ml2 is not None and ml2.status == "PROPOSED"
            and check.category is not None and check.category == ml2.proposed_category
            and ml2.approved_rule_preserved is False
        )
        sources = ml2.citations if proposed else check.citations
        reason = ml2.reason if ml2 and (proposed or check.category is None) else check.explanation
        missing = list(ml2.missing_context) if ml2 else []
        if not check.category and not missing:
            missing.append(reason or check.reason_code)
        dosage = ml2.dosage_check if ml2 else None
        dosage_check = (
            DosageCheck.model_validate(dosage.model_dump())
            if dosage and dosage.status in {"INSUFFICIENT_DATA", "NOT_CHECKED"}
            else DosageCheck(status="NOT_CHECKED", reason=None, missing_data=[])
        )
        yield PairContext(diagnosis=diagnoses[check.diagnosis_id]), PairInput(
            item_id=f"{item.item_id}:{check.diagnosis_id}", inn=item.mnn,
            category=check.category, classification_reason=reason,
            source_refs=list(dict.fromkeys(source.fragment_id for source in sources)),
            dosage_check=dosage_check, upstream_status="PROCESSED" if check.category else "PARTIAL",
            missing_data=missing,
        )
