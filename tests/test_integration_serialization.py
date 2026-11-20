"""Полнота результата и происхождение цитат при сохранении JSON."""

import pytest
from pydantic import ValidationError
from llm_rag.integration_contracts import IntegrationRequest


def data():
    return dict(
        schema_version="integration-request.1",
        request_id="R",
        diagnoses=[dict(diagnosis_id="D", name="Диагноз")],
        purchases=[dict(item_id="P", name="Средство")],
        links=[dict(item_id="P", diagnosis_id="D")],
    )


def test_result_roundtrip_and_exact_pair_coverage():
    from llm_rag.integration_contracts import IntegrationResult, UnprocessedPair

    request = IntegrationRequest.model_validate(data())
    result = IntegrationResult(
        request=request,
        unprocessed_pairs=[
            UnprocessedPair(request_id="R", item_id="P", diagnosis_id="D", reason="BUDGET_EXHAUSTED")
        ],
    )
    assert result.completeness == "PARTIAL"
    assert IntegrationResult.model_validate_json(result.model_dump_json()) == result
    for changes in [
        dict(unprocessed_pairs=[]),
        dict(unprocessed_pairs=result.unprocessed_pairs * 2),
        dict(schema_version="integration-result.2"),
    ]:
        with pytest.raises(ValidationError):
            IntegrationResult.model_validate(result.model_dump() | changes)


def test_result_rejects_foreign_pair():
    from llm_rag.integration_contracts import IntegrationResult

    with pytest.raises(ValidationError):
        IntegrationResult(
            request=IntegrationRequest.model_validate(data()),
            unprocessed_pairs=[
                dict(request_id="foreign", item_id="P", diagnosis_id="D", reason="BUDGET_EXHAUSTED")
            ],
        )


def test_inconsistent_category_basis_is_rejected():
    from llm_rag.integration_contracts import CategoryDecision

    with pytest.raises(ValidationError):
        CategoryDecision(
            upstream_rule_category="А", proposed_category="Б", classification_basis="APPROVED_RULE"
        )


def test_result_rejects_fabricated_money():
    from llm_rag.integration_contracts import IntegrationResult

    with pytest.raises(ValidationError):
        IntegrationResult(
            request=IntegrationRequest.model_validate(data()),
            unprocessed_pairs=[
                dict(request_id="R", item_id="P", diagnosis_id="D", reason="BUDGET_EXHAUSTED")
            ],
            costs={"totals": {"RUB": "999"}},
        )


def verified_result():
    from llm_rag.integration_contracts import IntegrationResult, IntegrationManifest, ResourceVersion
    from llm_rag.evidence_snapshot import EvidenceSnapshot, EvidenceDocument
    from hashlib import sha256
    from llm_rag.ml2.classifier import policy_fingerprint

    policy_version, policy_sha = policy_fingerprint()
    doc = EvidenceDocument(
        source_id="s",
        fragment_id="f",
        title="Источник",
        version="v1",
        url="https://example.org/source",
        copy_url=None,
        location="Раздел 1",
        text="Вещество упоминается.",
        diagnosis="Диагноз",
        inn="вещество",
        retrieved_at="2026-11-17T00:00:00Z",
    )
    snapshot = EvidenceSnapshot(snapshot_version="e1", documents=[doc])
    citation = dict(
        source_id="s",
        fragment_id="f",
        title=doc.title,
        version="v1",
        url=doc.url,
        copy_url=None,
        location=doc.location,
        quote=doc.text,
    )
    explanation = dict(
        request_id="R",
        item_id="P",
        diagnosis_id="D",
        category="А",
        ml2_status="PROPOSED",
        classification_basis="ML2_PROPOSAL",
        evidence_status="VERIFIED",
        comment="Проверено происхождение.",
        citations=[citation],
        reasons=[],
        missing_context=[],
        dosage_check=dict(status="NOT_CHECKED", reason=None, missing_data=[]),
        snapshot_version="e1",
    )
    return IntegrationResult(
        request=IntegrationRequest.model_validate(data()),
        completeness="COMPLETE",
        manifest=IntegrationManifest(
            clinical=ResourceVersion(
                version="e1", sha256=sha256(snapshot.model_dump_json().encode()).hexdigest()
            ),
            policy=ResourceVersion(version=policy_version, sha256=policy_sha),
        ),
        evidence_snapshot=snapshot,
        pairs=[
            dict(
                request_id="R",
                item_id="P",
                diagnosis_id="D",
                inn="Вещество",
                decision=dict(proposed_category="А", classification_basis="ML2_PROPOSAL"),
                evidence_status="VERIFIED",
                policy_version=policy_version,
                citations=[citation],
                explanation=explanation,
            )
        ],
    )


def test_normalized_inn_survives_result_serialization():
    from llm_rag.integration_contracts import IntegrationResult

    result = verified_result()
    assert IntegrationResult.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize(
    "mutation", ["copy_url", "title", "snapshot", "corpus", "category", "status", "public"]
)
def test_tampered_citation_explanation_or_public_report_is_rejected(mutation):
    from llm_rag.integration_contracts import IntegrationResult

    value = verified_result().model_dump()
    pair = value["pairs"][0]
    if mutation in {"copy_url", "title"}:
        pair["citations"][0][mutation] = "https://attacker.example" if mutation == "copy_url" else "Подмена"
        pair["explanation"]["citations"][0][mutation] = pair["citations"][0][mutation]
    elif mutation in {"snapshot", "corpus"}:
        pair["explanation"][
            {"snapshot": "snapshot_version", "corpus": "approved_corpus_version"}[mutation]
        ] = "foreign"
    elif mutation == "category":
        pair["explanation"]["category"] = "В"
    elif mutation == "status":
        pair["explanation"]["evidence_status"] = "UNCONFIRMED"
    else:
        if not value["human_report"]:
            value["human_report"] = {"items": [{"category": "А"}]}
        value["human_report"]["items"][0]["category"] = "В"
    with pytest.raises(ValidationError):
        IntegrationResult.model_validate(value)


@pytest.mark.parametrize("mutation", ["pair_policy", "request_pin"])
def test_result_rejects_inconsistent_policy_and_resource_pins(mutation):
    from llm_rag.integration_contracts import IntegrationResult

    value = verified_result().model_dump()
    if mutation == "pair_policy":
        value["pairs"][0]["policy_version"] = "foreign"
    else:
        value["request"]["versions"]["registry"] = "foreign"
    with pytest.raises(ValidationError):
        IntegrationResult.model_validate(value)
