"""Bounded evaluation of complete bundles using the existing classifier only."""

from dataclasses import replace

from .classifier import Assessment, ClassificationStatus
from .evidence_selection import evidence_bundles, fragment_key, pack_evidence, rank_bundles


def evaluate_packets(*, fragments, inn, diagnosis, context, limits, evaluate, complete):
    """Evaluate every packet or fail the pair; a partial search cannot yield a letter.

    `evaluate` still resolves the registry and validates each model response.
    A retry keeps the actual conditions and never supplies a desired assessment.
    """
    bundles = rank_bundles(
        evidence_bundles(fragments, inn, diagnosis, context, limits.get("checkpoint")),
        limits.get("similarities"),
    )
    packets = pack_evidence(
        bundles,
        inn=inn,
        diagnosis=diagnosis,
        context=context,
        max_context_bytes=limits["max_context_bytes"],
    )
    # A source that contains no provable role is still passed to the classifier.
    # No source text and no model response are written to diagnostics or logs.
    if not packets:
        packets = (tuple(fragments),)
    evaluated, initial, retries = [], [], 0
    for packet in packets:
        if limits.get("checkpoint"):
            limits["checkpoint"]()
        value = evaluate(packet, None)
        result = value.classification
        initial.append(result)
        if (
            complete is not None
            and retries == 0
            and result.model_assessment in {Assessment.MENTIONED_ONLY, Assessment.NOT_FOUND}
            and any(b.priority == 1 and set(b.identity) <= {fragment_key(f) for f in packet} for b in bundles)
        ):

            def retry(system, user):
                return complete(
                    system + "\nПовторная сверка: рассмотрите прямые и групповые основания, "
                    "их связи и ограничения во всех переданных фрагментах. "
                    "Сохраните неизвестные условия; отсутствие данных не подтверждает применимость.",
                    user,
                )

            value = evaluate(packet, retry)
            retries += 1
        evaluated.append(value)

    base = evaluated[0]
    results = [v.classification for v in evaluated]
    categories = {r.proposed_category for r in results if r.proposed_category}
    assessments = {r.assessment for r in results}
    failed = next(
        (
            r
            for r in results
            if r.status
            in {
                ClassificationStatus.PROVIDER_ERROR,
                ClassificationStatus.INVALID_RESPONSE,
                ClassificationStatus.SOURCE_ERROR,
            }
        ),
        None,
    )
    needs_context = any(r.status == ClassificationStatus.NEEDS_CONTEXT for r in results)
    conflict = (
        len(categories) > 1
        or Assessment.CONFLICTING_EVIDENCE in assessments
        or bool(categories and Assessment.EXPLICITLY_NOT_RECOMMENDED in assessments)
    )
    citations = {tuple(vars(c).items()): c for r in results for c in r.citations}
    merged = replace(
        base.classification,
        citations=tuple(citations.values()),
        conditions=tuple(sorted({c for r in results for c in r.conditions})),
        missing_context=tuple(sorted({c for r in results for c in r.missing_context})),
    )
    if failed:
        merged = replace(
            merged,
            proposed_category=None,
            status=failed.status,
            assessment=failed.assessment,
            review_reason=failed.review_reason,
        )
    elif conflict:
        merged = replace(
            merged,
            proposed_category=None,
            status=ClassificationStatus.CONFLICT,
            assessment=Assessment.CONFLICTING_EVIDENCE,
            review_reason="Основания разных пакетов противоречат друг другу.",
        )
    elif needs_context:
        merged = replace(
            merged,
            proposed_category=None,
            status=ClassificationStatus.NEEDS_CONTEXT,
            assessment=Assessment.INSUFFICIENT_CONTEXT,
            review_reason="Для части оснований недостаточно условий применимости.",
        )
    elif categories:
        positive = next(r for r in results if r.proposed_category)
        merged = replace(
            merged,
            proposed_category=positive.proposed_category,
            assessment=positive.assessment,
            status=positive.status,
            model_assessment=positive.model_assessment,
            review_reason=positive.review_reason,
        )
    elif len(results) > 1:
        # Do not let an uninformative first packet hide a later explicit refusal.
        refusal = next((r for r in results if r.assessment == Assessment.EXPLICITLY_NOT_RECOMMENDED), None)
        if refusal:
            merged = replace(
                merged,
                assessment=refusal.assessment,
                status=refusal.status,
                model_assessment=refusal.model_assessment,
                review_reason=refusal.review_reason,
            )
    first = initial[0]
    rejected = any(r.model_assessment and r.model_assessment != r.assessment for r in results)
    stage = (
        "PROVIDER_ERROR"
        if failed
        else "CONFLICT"
        if conflict
        else "ACCEPTED"
        if merged.proposed_category
        else "VALIDATOR_REJECTED"
        if rejected
        else "MODEL_OMISSION"
        if retries
        else "NO_CANDIDATES"
        if not fragments
        else "REVIEW_REQUIRED"
    )
    diagnostics = {
        "schema_version": "classification-diagnostics.1",
        "stage": stage,
        "model_assessment": first.model_assessment.value if first.model_assessment else None,
        "assessment": merged.assessment.value if merged.assessment else None,
        "candidate_count": len(fragments),
        "bundle_count": len(bundles),
        "packet_count": len(packets),
        "retry_count": retries,
        "fragment_refs": [
            dict(source_id=k[0], version=k[1], fragment_id=k[2])
            for k in sorted({fragment_key(f) for p in packets for f in p})
        ],
    }
    return replace(base, classification=merged, classification_diagnostics=diagnostics)
