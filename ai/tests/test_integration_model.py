"""Сетевой фасад проверяется локальным адаптером, без оплачиваемого API."""

from contextlib import contextmanager
from datetime import date

from tests.test_integration import request
from tests.ml2.test_ml2 import fragment, complete
from llm_rag.evidence_snapshot import EvidenceSnapshot, EvidenceDocument
from llm_rag.ml2.registry import RegistrySnapshot, RegistryEntry, RegistrySource


class FakeClient:
    scope = "GIGACHAT_API_B2B"
    verify_ssl_certs = True

    def __init__(self):
        from llm_rag.network_policy import NetworkPolicy

        self.network_policy = NetworkPolicy(diagnoses=("J45.9",), inns=("Тестовое вещество",))
        self.closed = False
        self.calls = 0
        self.timeouts = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    @contextmanager
    def request_limits(self, *, timeout, max_tokens):
        self.timeouts.append(timeout)
        yield

    def complete_json(self, payload, system, schema):
        self.calls += 1
        assert "clinical_context" not in payload
        return complete(None, None)

    def generate(self, payload):
        from tests.test_staff_report import ReportProvider

        self.calls += 1
        return ReportProvider().generate(payload)


def resources(client):
    from llm_rag.integration import IntegrationResources

    f = fragment()
    source = RegistrySource(
        title="Реестр",
        url="https://grls.rosminzdrav.ru/GRLS.aspx",
        version="r1",
        location="1",
        retrieved_at=date(2026, 11, 17),
        quote="Тестовое вещество",
    )
    registry = RegistrySnapshot(
        version="r1",
        as_of=date(2026, 11, 17),
        registrations=(
            RegistryEntry(
                registration="1", trade_name="Неизвестный препарат", mnn="Тестовое вещество", source=source
            ),
        ),
    )
    snapshot = EvidenceSnapshot(
        snapshot_version="s1",
        documents=[
            EvidenceDocument(
                **vars(f), diagnosis="J45.9", inn="Тестовое вещество", retrieved_at="2026-11-17T00:00:00Z"
            )
        ],
    )
    return IntegrationResources(registry=registry, evidence=snapshot, provider_factory=lambda: client)


def model_request():
    value = request(1)
    value.diagnoses[0].name = "J45.9"
    return value


def test_model_classification_and_staff_share_budget_and_close_client():
    from llm_rag.integration import _run, IntegrationOptions

    client = FakeClient()
    result = _run(
        model_request(),
        resources=resources(client),
        options=IntegrationOptions(max_tokens=100000),
    )
    assert client.closed
    assert result.pairs[0].decision.proposed_category == "Б"
    assert result.usage.calls == client.calls == 2
    assert result.pairs[0].generation_mode == "MODEL"
    assert result.human_report["items"][0]["questions"]


def test_public_recommendation_respects_local_age_and_requires_clinical_review():
    from dataclasses import replace
    from decimal import Decimal
    from llm_rag.integration import _run, IntegrationOptions

    for age in (None, Decimal("38"), Decimal("8")):
        value = model_request()
        value.diagnoses[0].conditions.age_years = age
        local = resources(FakeClient())
        doc = local.evidence.documents[0].model_copy(update={"required_context": {"age_group": "Взрослые"}})
        local = replace(local, evidence=EvidenceSnapshot(snapshot_version="adult-only", documents=[doc]))
        result = _run(
            value,
            resources=local,
            options=IntegrationOptions(max_tokens=100000),
        )
        pair = result.pairs[0]
        if age == 8:
            assert pair.citations == [] and pair.decision.proposed_category is None
            assert "NO_EVIDENCE" in pair.reasons
        else:
            assert pair.citations and pair.evidence_status == "INSUFFICIENT_DATA"
            assert "clinical_applicability" in pair.explanation.missing_context
            assert ("context.age_group" in pair.explanation.missing_context) == (age is None)


def test_token_budget_stops_before_external_invocation():
    from llm_rag.integration import _run, IntegrationOptions

    client = FakeClient()
    result = _run(
        model_request(),
        resources=resources(client),
        options=IntegrationOptions(max_tokens=0),
    )
    assert client.closed and client.calls == 0
    assert result.unprocessed_pairs[0].reason == "BUDGET_EXHAUSTED"


def test_staff_budget_failure_preserves_classification():
    from llm_rag.integration import _run, IntegrationOptions

    client = FakeClient()
    result = _run(
        model_request(),
        resources=resources(client),
        options=IntegrationOptions(max_calls=1, max_tokens=100000),
    )
    assert result.pairs[0].decision.proposed_category == "Б"
    assert result.pairs[0].generation_mode == "FALLBACK"
    assert result.completeness == "PARTIAL"
    assert client.closed and client.calls == 1


def test_vector_search_is_used_in_excel_facade_without_cross_diagnosis_pairs():
    from dataclasses import replace
    from llm_rag.integration import _run, IntegrationOptions
    from tests.test_vectors import FixedEmbedder

    engine = FixedEmbedder()
    value = model_request()
    value.diagnoses[0].name = "j45.9"
    local = replace(resources(FakeClient()), embedding_factory=lambda **_: engine)
    result = _run(value, resources=local, options=IntegrationOptions())
    assert len(engine.document_batches) == 1 and len(engine.queries) == 1
    assert result.retrieval_info.embedding_model == "test-only-vectors"
    assert result.retrieval_info.method == "vector"
    assert result.usage.calls == 2


def test_client_close_failure_preserves_completed_result():
    from llm_rag.integration import _run, IntegrationOptions

    class ClosingFailure(FakeClient):
        def __exit__(self, *_):
            self.closed = True
            raise OSError("PRIVATE close error")

    client = ClosingFailure()
    result = _run(
        model_request(),
        resources=resources(client),
        options=IntegrationOptions(max_tokens=100000),
    )
    assert result.pairs[0].decision.proposed_category == "Б"
    assert result.lifecycle_status == "CLOSE_FAILED"
    assert "PRIVATE" not in result.model_dump_json()


def test_same_fragment_id_in_two_editions_survives_model_and_vector_path():
    from dataclasses import replace
    from llm_rag.integration import _run, IntegrationOptions
    from tests.test_vectors import FixedEmbedder

    class DynamicClient(FakeClient):
        def complete_json(self, payload, system, schema):
            self.calls += 1
            value = complete(None, None)
            value["evidence"][0]["fragment_id"] = payload["fragments"][0]["fragment_id"]
            return value

    client = DynamicClient()
    local = resources(client)
    docs = local.evidence.documents
    snapshot = EvidenceSnapshot(
        snapshot_version="s2", documents=docs + [docs[0].model_copy(update={"version": "edition-2"})]
    )
    engine = FixedEmbedder()
    local = replace(local, evidence=snapshot, embedding_factory=lambda **_: engine)
    result = _run(
        model_request(),
        resources=local,
        options=IntegrationOptions(max_tokens=100000),
    )
    assert result.pairs[0].decision.proposed_category == "Б"
    assert len(engine.document_batches[0]) == 2
    assert result.pairs[0].citations[0].fragment_id == docs[0].fragment_id
    assert result.pairs[0].citations[0].version in {docs[0].version, "edition-2"}


def test_context_limit_stops_before_any_model_call():
    from llm_rag.integration import _run, IntegrationOptions

    client = FakeClient()
    result = _run(
        model_request(),
        resources=resources(client),
        options=IntegrationOptions(max_context_bytes=0),
    )
    assert client.closed and client.calls == 0
    assert result.unprocessed_pairs[0].reason == "CONTEXT_LIMIT"


def test_staff_analysis_cannot_change_pair_identity():
    import pytest
    from pydantic import ValidationError
    from llm_rag.integration import _run, IntegrationOptions
    from llm_rag.integration_contracts import IntegrationResult

    result = _run(
        model_request(),
        resources=resources(FakeClient()),
        options=IntegrationOptions(max_tokens=100000),
    )
    value = result.model_dump()
    value["pairs"][0]["staff_analysis"]["pair_id"] = "foreign"
    with pytest.raises(ValidationError):
        IntegrationResult.model_validate(value)


def test_repeated_checks_reuse_calls_without_merging_original_ids():
    from llm_rag.integration import _run, IntegrationOptions
    from llm_rag.integration_contracts import IntegrationLink

    value = model_request()
    value.purchases.append(value.purchases[0].model_copy(update={"item_id": "Q"}))
    value.links.append(IntegrationLink(item_id="Q", diagnosis_id="D0"))
    client = FakeClient()
    result = _run(
        value,
        resources=resources(client),
        options=IntegrationOptions(max_tokens=100000),
    )
    assert client.calls == result.usage.calls == 2
    assert result.usage.cache_hits == 2
    assert [p.item_id for p in result.pairs] == ["P", "Q"]
    assert str(result.costs.totals["RUB"]) == "200.20"
    assert result.pairs[0].staff_analysis.pair_id != result.pairs[1].staff_analysis.pair_id


def test_different_local_conditions_do_not_share_model_cache():
    from llm_rag.integration import _run, IntegrationOptions
    from llm_rag.integration_contracts import IntegrationLink

    value = model_request()
    value.diagnoses[0].conditions.age_years = "20"
    second = value.diagnoses[0].model_copy(deep=True, update={"diagnosis_id": "D1"})
    second.conditions.age_years = "30"
    value.diagnoses.append(second)
    value.purchases.append(value.purchases[0].model_copy(update={"item_id": "Q"}))
    value.links.append(IntegrationLink(item_id="Q", diagnosis_id="D1"))
    client = FakeClient()
    result = _run(
        value,
        resources=resources(client),
        options=IntegrationOptions(max_tokens=150000),
    )
    assert client.calls == 4 and result.usage.cache_hits == 0


def test_model_mentioned_only_is_review_needed_not_model_disabled():
    from llm_rag.integration import _run, IntegrationOptions

    class MentionedClient(FakeClient):
        def complete_json(self, payload, system, schema):
            value = super().complete_json(payload, system, schema)
            value["assessment"] = "MENTIONED_ONLY"
            return value

    client = MentionedClient()
    result = _run(
        model_request(),
        resources=resources(client),
        options=IntegrationOptions(max_tokens=100000),
    )
    assert result.pairs[0].decision.proposed_category is None
    assert result.pairs[0].reasons == ["NEEDS_REVIEW"]
    assert result.pairs[0].generation_mode == "MODEL"


def test_concurrent_model_requests_keep_ids_budgets_and_clients_separate():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from llm_rag.integration import _run, IntegrationOptions

    barrier = Barrier(2)

    class OverlappingClient(FakeClient):
        def complete_json(self, *args):
            barrier.wait(timeout=5)
            return super().complete_json(*args)

    clients = [OverlappingClient(), OverlappingClient()]

    def run(index):
        value = model_request()
        value.request_id = f"request-{index}"
        return _run(
            value,
            resources=resources(clients[index]),
            options=IntegrationOptions(
                max_calls=index + 1, max_tokens=100000
            ),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, range(2)))
    assert [c.calls for c in clients] == [1, 2]
    assert all(c.closed for c in clients)
    assert [r.usage.calls for r in results] == [1, 2]
    assert [r.pairs[0].request_id for r in results] == ["request-0", "request-1"]
    assert [r.pairs[0].generation_mode for r in results] == ["FALLBACK", "MODEL"]
