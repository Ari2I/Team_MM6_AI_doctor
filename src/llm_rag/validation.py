"""Проверка точных цитат и полноты покрытия разрешёнными утверждениями."""

from dataclasses import dataclass

from pydantic import ValidationError

from llm_rag.contracts import AnalysisItem, Citation, Model, Text, VerifiedClaim
from llm_rag.corpus import Corpus
from llm_rag.evidence import EvidenceClaim, EvidencePacket


class GenerationError(Exception):
    """Провайдер вернул ответ непригодной структуры; медицинский вывод не сформирован."""


class GeneratedClaim(Model):
    claim_id: Text
    text: Text
    fragment_id: Text
    quote: Text


class GeneratedResponse(Model):
    item_id: Text
    claims: list[GeneratedClaim]


@dataclass(frozen=True)
class ValidationOutcome:
    claims: tuple[VerifiedClaim, ...]
    complete: bool
    rejected_count: int


def citation_for(claim: EvidenceClaim, corpus: Corpus) -> Citation:
    source = next(source for source in corpus.sources if source.source_id == claim.fragment.source_id)
    return Citation(
        source_id=source.source_id, fragment_id=claim.fragment.fragment_id, title=source.title,
        source_type=source.source_type, version=source.version,
        location=claim.fragment.location, quote=claim.assertion.quote,
    )


def validate_generation(
    raw: dict, packet: EvidencePacket, item: AnalysisItem, corpus: Corpus
) -> ValidationOutcome:
    try:
        response = GeneratedResponse.model_validate(raw)
    except ValidationError:
        raise GenerationError("INVALID_RESPONSE_SCHEMA") from None
    if response.item_id != item.item_id:
        raise GenerationError("RESPONSE_ITEM_MISMATCH")
    allowed = {claim.assertion.claim_id: claim for claim in packet.claims}
    accepted: dict[str, VerifiedClaim] = {}
    rejected = 0
    for generated in response.claims:
        evidence = allowed.get(generated.claim_id)
        if evidence is None:
            rejected += 1
            continue
        assertion, fragment = evidence.assertion, evidence.fragment
        if (
            generated.claim_id in accepted
            or generated.fragment_id != fragment.fragment_id
            or generated.quote != assertion.quote
            or generated.quote not in fragment.text
            or generated.text != assertion.text
        ):
            rejected += 1
            continue
        accepted[generated.claim_id] = VerifiedClaim(
            claim_id=generated.claim_id, kind=assertion.kind, text=assertion.text,
            citations=[citation_for(evidence, corpus)],
        )
    claims = tuple(accepted.values())
    return ValidationOutcome(
        claims=claims, complete=packet.required_kinds.issubset({c.kind for c in claims}),
        rejected_count=rejected,
    )
