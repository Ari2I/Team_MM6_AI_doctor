"""Regressions for false CONTEXT_LIMIT before any model or embedding call."""

from dataclasses import replace

import pytest

from llm_rag.ml2.classifier import _quote_options
from llm_rag.ml2.clinicalrecs import collect_fragments_for_inn
from tests.ml2.test_category_retrieval_regressions import recommendation
from tests.test_evidence_packets import PacketClient
from tests.test_integration_model import model_request, resources
from tests.test_vectors import FixedEmbedder


@pytest.mark.parametrize(
    "role",
    [
        "Будесонид рекомендуется для лечения заболевания",
        "Будесонид не рекомендуется при синтетическом состоянии",
    ],
)
def test_complete_sentence_can_exceed_neighbour_window_without_losing_condition(role):
    quote = role + ", " + "дополнительное условие " * 60 + "только у взрослых."
    assert 900 < len(quote) < 3000
    rec = recommendation([("synthetic paragraph", quote)])
    fragments = collect_fragments_for_inn(rec, "будесонид")
    assert any(f.text == quote and quote in _quote_options(f.text) for f in fragments)


def test_unrelated_oversized_group_cannot_block_known_ingredient():
    quote = "Будесонид рекомендуется для лечения заболевания."
    rec = recommendation(
        [
            ("unrelated", "АБВ рекомендуется при " + "синтетическом условии " * 200 + "."),
            ("target", quote),
        ]
    )
    fragments = collect_fragments_for_inn(rec, "будесонид")
    assert [f.text for f in fragments] == [quote]


def test_group_membership_and_long_recommendation_are_kept_together():
    membership = "КДБА (сальбутамол) приведены в синтетическом перечне."
    quote = "КДБА рекомендуется для лечения заболевания при " + "синтетическом условии " * 60 + "."
    rec = recommendation([("definition", membership), ("role", quote)])
    fragments = collect_fragments_for_inn(rec, "сальбутамол")
    assert {f.text for f in fragments} == {membership, quote}


def test_late_quote_option_is_not_hidden_by_many_short_sentences():
    quote = "Будесонид рекомендуется для лечения заболевания."
    text = "Синтетика. " * 25 + quote
    rec = recommendation([("dense paragraph", text)])
    fragments = collect_fragments_for_inn(rec, "будесонид")
    assert any(quote in _quote_options(f.text) for f in fragments)


class BoundedEmbedder(FixedEmbedder):
    max_text_bytes = 4000

    def embed_documents(self, texts):
        assert all(len(t.encode()) <= self.max_text_bytes for t in texts)
        return super().embed_documents(texts)


def test_large_complete_evidence_reaches_classifier_without_embedding_truncation():
    from llm_rag.evidence_snapshot import EvidenceSnapshot
    from llm_rag.integration import _run

    long_quote = "Тестовое вещество рекомендуется для лечения заболевания при " + "условии " * 300 + "."
    assert len(long_quote) < 3000 and len(long_quote.encode()) > 4000
    client = PacketClient()
    local = resources(client)
    original = local.evidence.documents[0]
    documents = [
        original.model_copy(update={"fragment_id": "long", "text": long_quote}),
        original.model_copy(update={"fragment_id": "short", "text": "Тестовое вещество упомянуто."}),
    ]
    embedder = BoundedEmbedder()
    local = replace(
        local,
        evidence=EvidenceSnapshot(snapshot_version="long-evidence", documents=documents),
        embedding_factory=lambda **_: embedder,
    )
    result = _run(model_request(), resources=local)
    assert result.completeness == "COMPLETE" and not result.unprocessed_pairs
    assert result.pairs[0].decision.proposed_category == "А"
    sent = [f["text"] for packet in client.payloads for f in packet["fragments"]]
    assert long_quote in sent
    assert embedder.document_batches and all(
        long_quote not in t for batch in embedder.document_batches for t in batch
    )
    assert result.pairs[0].classification_diagnostics.embedding_unranked_count == 1


def test_all_oversized_embedding_inputs_do_not_claim_vector_search():
    from llm_rag.evidence_snapshot import EvidenceSnapshot
    from llm_rag.integration import _run

    quote = "Тестовое вещество рекомендуется для лечения заболевания при " + "условии " * 300 + "."
    client = PacketClient()
    local = resources(client)
    document = local.evidence.documents[0].model_copy(update={"text": quote})
    embedder = BoundedEmbedder()
    local = replace(
        local,
        evidence=EvidenceSnapshot(snapshot_version="long-only", documents=[document]),
        embedding_factory=lambda **_: embedder,
    )
    result = _run(model_request(), resources=local)
    assert result.pairs[0].decision.proposed_category == "А"
    assert result.retrieval_info.method == "lexical"
    assert not embedder.document_batches and not embedder.queries
    diagnostic = result.pairs[0].classification_diagnostics
    assert diagnostic.embedding_ranked_count == 0 and diagnostic.embedding_unranked_count == 1


def test_embedding_input_limit_is_public_and_does_not_change_default():
    from llm_rag.embeddings import GigaChatEmbedder

    assert GigaChatEmbedder().max_text_bytes == 4000
    assert GigaChatEmbedder(max_text_bytes=1024).max_text_bytes == 1024


def test_public_runtime_builds_snapshot_before_model_for_long_valid_source(monkeypatch, tmp_path):
    from llm_rag import runtime
    from llm_rag.integration import analyze_request
    from llm_rag.ml2 import official

    quote = "Тестовое вещество рекомендуется для лечения заболевания при " + "условии " * 130 + "."
    client = PacketClient()
    registry = resources(client).registry
    embedder = BoundedEmbedder()

    def public_json(operation, *, deadline, **params):
        if operation == "GetJsonClinrecs":
            return [
                dict(
                    id="7_2",
                    age=3,
                    status=0,
                    apply_status="Применяется",
                    name="J45.9",
                    Mkb=["J45"],
                    publish_date="2026-01-01",
                )
            ]
        return dict(
            id="7_2",
            adult=True,
            child=True,
            name="J45.9",
            status=0,
            apply_status="Применяется",
            publish_date="2026-01-01",
            mkb="J45",
            obj={"sections": [dict(title="3. Лечение", content=f"<p>{quote}</p>")]},
        )

    monkeypatch.setattr(official, "_get", public_json)
    monkeypatch.setattr(runtime, "_registry", lambda *args: registry)
    monkeypatch.setattr(runtime, "source_root", lambda: None)
    monkeypatch.setattr(runtime, "default_data_dir", lambda: tmp_path)
    monkeypatch.setattr(runtime, "ML2GigaChatProvider", lambda **_: client)
    monkeypatch.setattr(runtime, "corpus_embedder", lambda *args, **kwargs: embedder)
    monkeypatch.delenv("RAG_NETWORK_TERMS", raising=False)
    request = model_request()
    request.diagnoses[0].icd10 = "J45.9"
    result = analyze_request(request)
    assert result.completeness == "COMPLETE" and not result.unprocessed_pairs
    assert result.evidence_snapshot.documents[0].text == quote
    assert result.pairs[0].decision.proposed_category == "А"
    assert client.payloads and embedder.document_batches
