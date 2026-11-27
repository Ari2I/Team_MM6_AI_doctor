"""Technical labels are synthetic, not clinical expert acceptance."""

from dataclasses import replace

import pytest

from llm_rag.evidence_snapshot import EvidenceSnapshot
from llm_rag.integration import IntegrationOptions, _run
from llm_rag.integration_contracts import IntegrationResult
from tests.test_integration_model import FakeClient, model_request, resources


class PacketClient(FakeClient):
    def __init__(self, *, omit_first=False, fail_second=False):
        super().__init__()
        self.payloads = []
        self.omit_first = omit_first
        self.fail_second = fail_second

    def complete_json(self, payload, system, schema):
        self.calls += 1
        self.payloads.append(payload)
        if self.fail_second and len(self.payloads) == 2:
            raise OSError("private provider detail")
        fragment = payload["fragments"][0]
        text = fragment["text"]
        assessment = (
            "EXPLICITLY_NOT_RECOMMENDED"
            if "не рекомендуется" in text
            else "SUPPORTIVE_TREATMENT"
            if "вспомогательн" in text
            else "OPTIONAL_PRODUCT"
            if "витамин" in text
            else "CORE_TREATMENT"
        )
        if self.omit_first and len(self.payloads) == 1:
            assessment = "MENTIONED_ONLY"
        return {
            "assessment": assessment,
            "conditions": [],
            "missing_context": [],
            "evidence": [{"fragment_id": fragment["fragment_id"], "quote": fragment["quote_options"][0]}],
        }


def run_packets(texts, client=None, **options):
    client = client or PacketClient()
    local = resources(client)
    original = local.evidence.documents[0]
    documents = [
        original.model_copy(
            update={
                "fragment_id": f"fragment-{i}",
                "location": f"synthetic paragraph {i}",
                "text": text,
            }
        )
        for i, text in enumerate(texts)
    ]
    local = replace(local, evidence=EvidenceSnapshot(snapshot_version="packets", documents=documents))
    return _run(model_request(), resources=local, options=IntegrationOptions(**options)), client


def test_more_than_twenty_candidates_are_evaluated_and_diagnostics_are_saved():
    texts = [f"Тестовое вещество рекомендуется для лечения заболевания. Пример {i}." for i in range(25)]
    result, client = run_packets(texts)
    assert not result.unprocessed_pairs
    assert result.pairs[0].decision.proposed_category == "А"
    assert len(client.payloads) == 2
    assert {f["fragment_id"] for p in client.payloads for f in p["fragments"]} == {
        f"fragment-{i}" for i in range(25)
    }
    saved = IntegrationResult.model_validate_json(result.model_dump_json())
    diagnostic = saved.pairs[0].classification_diagnostics
    assert diagnostic.schema_version == "classification-diagnostics.1"
    assert diagnostic.packet_count == 2 and diagnostic.candidate_count == 25


def test_negative_in_separate_packet_prevents_positive_category():
    result, client = run_packets(
        [
            "Тестовое вещество рекомендуется для лечения заболевания.",
            "Тестовое вещество не рекомендуется для лечения заболевания.",
        ]
    )
    assert len(client.payloads) == 2
    assert result.pairs[0].decision.proposed_category is None
    assert result.pairs[0].classification_diagnostics.stage == "CONFLICT"


def test_incomplete_search_budget_cannot_return_positive_category():
    result, _ = run_packets(
        [f"Тестовое вещество рекомендуется для лечения заболевания. Пример {i}." for i in range(25)],
        max_calls=1,
    )
    assert not result.pairs
    assert result.unprocessed_pairs[0].reason == "BUDGET_EXHAUSTED"


def test_provider_failure_in_later_packet_cannot_return_positive_category():
    result, _ = run_packets(
        [f"Тестовое вещество рекомендуется для лечения заболевания. Пример {i}." for i in range(25)],
        client=PacketClient(fail_second=True),
    )
    assert result.pairs[0].decision.proposed_category is None
    assert "AI_UNAVAILABLE" in result.pairs[0].reasons


def test_different_categories_in_separate_packets_are_not_combined():
    texts = [f"Тестовое вещество рекомендуется для лечения заболевания. Пример {i}." for i in range(21)]
    texts[-1] = "Тестовое вещество рекомендуется как вспомогательное лечение."
    # Selection order is canonical; force small packets so both hypotheses are evaluated.
    result, _ = run_packets(texts, max_context_bytes=700)
    assert result.pairs[0].decision.proposed_category is None
    assert result.pairs[0].classification_diagnostics.stage == "CONFLICT"


def test_old_result_without_diagnostics_is_still_readable():
    result, _ = run_packets(["Тестовое вещество рекомендуется для лечения заболевания."])
    value = result.model_dump()
    value["pairs"][0].pop("classification_diagnostics")
    assert IntegrationResult.model_validate(value).pairs[0].classification_diagnostics is None


def test_one_retry_uses_actual_conditions_without_forcing_assessment():
    result, client = run_packets(
        [
            "Тестовое вещество рекомендуется для лечения заболевания.",
        ],
        client=PacketClient(omit_first=True),
    )
    assert len(client.payloads) == 2
    assert result.pairs[0].decision.proposed_category == "А"
    assert result.pairs[0].classification_diagnostics.retry_count == 1
    assert result.pairs[0].classification_diagnostics.model_assessment == "MENTIONED_ONLY"


@pytest.mark.parametrize(
    "text,category",
    [
        ("Тестовое вещество рекомендуется для лечения заболевания.", "А"),
        ("Тестовое вещество рекомендуется как вспомогательное лечение.", "Б"),
        ("Тестовое вещество описано как витамин.", "В"),
        ("Тестовое вещество не рекомендуется для лечения заболевания.", None),
    ],
)
def test_synthetic_labels_require_validated_literal_citation(text, category):
    result, _ = run_packets([text])
    assert result.pairs[0].decision.proposed_category == category
    if category:
        assert result.pairs[0].citations[0].quote == text
