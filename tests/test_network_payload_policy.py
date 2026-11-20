"""Все поля заявки за пределами явного сетевого набора остаются локальными."""

import pytest


def test_generation_payload_has_temporary_id_and_no_private_fields():
    from llm_rag.network_policy import NetworkPolicy, prepare_payload

    payload = dict(
        schema_version="1.0",
        corpus_version="v1",
        item=dict(
            item_id="PRIVATE-ID",
            diagnosis="Астма",
            inn="Сальбутамол",
            category="А",
            context={"name": "PRIVATE"},
            original_dosage="PRIVATE",
            dosage_check={"reason": "PRIVATE"},
            classification_reason="PRIVATE",
        ),
        evidence=[],
    )
    safe, restore = prepare_payload(payload, NetworkPolicy(diagnoses=("Астма",), inns=("Сальбутамол",)))
    assert "PRIVATE" not in str(safe)
    assert safe["item"]["item_id"] != "PRIVATE-ID"
    assert restore({"item_id": safe["item"]["item_id"], "claims": []})["item_id"] == "PRIVATE-ID"


def test_unapproved_term_is_rejected_before_external_call():
    from llm_rag.network_policy import NetworkPolicy, prepare_payload
    from llm_rag.providers.base import ProviderError

    with pytest.raises(ProviderError):
        prepare_payload(
            dict(
                schema_version="1.0",
                item=dict(item_id="ID", diagnosis="Астма; Иванов Иван", inn=None),
                evidence=[],
            ),
            NetworkPolicy(diagnoses=("Астма",)),
        )


def test_classification_context_is_always_local():
    from llm_rag.network_policy import NetworkPolicy, prepare_classification

    safe = prepare_classification(
        dict(diagnosis="Астма", inn="Сальбутамол", clinical_context={"age": "PRIVATE"}, fragments=[]),
        NetworkPolicy(diagnoses=("Астма",), inns=("Сальбутамол",)),
    )
    assert "clinical_context" not in safe
    assert "PRIVATE" not in str(safe)


def test_missing_policy_does_not_authorize_free_text():
    from llm_rag.network_policy import prepare_payload
    from llm_rag.providers.base import ProviderError

    with pytest.raises(ProviderError):
        prepare_payload(
            dict(schema_version="1.0", item=dict(item_id="ID", diagnosis="Астма", inn=None), evidence=[]),
            None,
        )


def test_gateway_transport_never_receives_private_fields():
    import httpx
    from llm_rag.providers import JsonApiProvider
    from llm_rag.network_policy import NetworkPolicy

    calls = []

    def handle(request):
        calls.append(request.content.decode())
        return httpx.Response(200, json={"item_id": "position-1", "claims": []})

    provider = JsonApiProvider(
        "https://example.org",
        "token",
        enabled=True,
        network_policy=NetworkPolicy(diagnoses=("Астма",)),
        transport=httpx.MockTransport(handle),
    )
    result = provider.generate(
        dict(
            schema_version="1.0",
            item=dict(
                item_id="PRIVATE",
                diagnosis="Астма",
                context={"secret": "PRIVATE"},
                original_dosage="PRIVATE",
                dosage_check={"reason": "PRIVATE"},
            ),
            evidence=[],
        )
    )
    assert result["item_id"] == "PRIVATE"
    assert len(calls) == 1 and "PRIVATE" not in calls[0]


def test_staff_payload_drops_private_conditions_and_reasons():
    from llm_rag.network_policy import NetworkPolicy, prepare_payload

    facts = dict(
        diagnosis="Астма",
        inn=None,
        category=None,
        ml2_status="NO_EVIDENCE",
        evidence_status="UNCONFIRMED",
        classification_basis="NONE",
        age_provided="PRIVATE",
        missing_information=["PRIVATE"],
        unverified_conditions=[{"text": "PRIVATE"}],
        ml2_status_meaning="PRIVATE",
    )
    safe, restore = prepare_payload(
        dict(schema_version="staff-facts.2", pairs=[dict(pair_id="PRIVATE", facts=facts, citations=[])]),
        NetworkPolicy(diagnoses=("Астма",)),
    )
    assert "PRIVATE" not in str(safe)
    assert (
        restore({"recommendations": [{"pair_id": "position-1"}]})["recommendations"][0]["pair_id"]
        == "PRIVATE"
    )


def test_classifier_prompt_does_not_copy_arbitrary_context():
    import json
    from llm_rag.ml2.classifier import ClassificationRequest, prompt_for
    from tests.ml2.test_ml2 import fragment

    _, user = prompt_for(
        ClassificationRequest(
            "PRIVATE",
            "PRIVATE",
            "J45.9",
            "Тестовое вещество",
            (fragment(),),
            {"name": "PRIVATE", "age": "PRIVATE"},
        )
    )
    assert "PRIVATE" not in user
    assert "clinical_context" not in json.loads(user)


def test_nested_fragment_and_citation_fields_are_not_sent():
    from llm_rag.network_policy import NetworkPolicy, prepare_classification, prepare_payload

    policy = NetworkPolicy(diagnoses=("Астма",), inns=("Вещество",))
    value = prepare_classification(
        dict(
            diagnosis="Астма",
            inn="Вещество",
            fragments=[
                dict(
                    fragment_id="f",
                    source_id="s",
                    title="Источник",
                    version="1",
                    location="1",
                    text="Публичный текст",
                    private_note="PRIVATE",
                )
            ],
        ),
        policy,
    )
    assert "PRIVATE" not in str(value)
    facts = dict(
        diagnosis="Астма",
        inn="Вещество",
        category=None,
        ml2_status="NO_EVIDENCE",
        evidence_status="UNCONFIRMED",
        classification_basis="NONE",
    )
    safe, _ = prepare_payload(
        dict(
            schema_version="staff-facts.2",
            pairs=[
                dict(
                    pair_id="1",
                    facts=facts,
                    citations=[
                        dict(
                            source_id="s",
                            fragment_id="f",
                            version="1",
                            quote="Текст",
                            url="https://example.org",
                            location="1",
                            private_note="PRIVATE",
                        )
                    ],
                )
            ],
        ),
        policy,
    )
    assert "PRIVATE" not in str(safe)


def test_nested_object_inside_allowed_source_field_is_rejected():
    from llm_rag.network_policy import NetworkPolicy, prepare_classification, prepare_payload
    from llm_rag.providers.base import ProviderError

    policy = NetworkPolicy(diagnoses=("Астма",), inns=("Вещество",))
    with pytest.raises(ProviderError):
        prepare_classification(
            dict(
                diagnosis="Астма",
                inn="Вещество",
                fragments=[
                    dict(
                        fragment_id="f",
                        source_id="s",
                        title={"private_note": "PRIVATE"},
                        version="1",
                        location="1",
                        text="Текст",
                    )
                ],
            ),
            policy,
        )
    with pytest.raises(ProviderError):
        prepare_payload(
            dict(
                schema_version="1.0",
                item=dict(item_id="1", diagnosis="Астма", inn="Вещество"),
                evidence=[
                    dict(claim_id="1", fragment_id="f", text="Текст", quote={"private_note": "PRIVATE"})
                ],
            ),
            policy,
        )
