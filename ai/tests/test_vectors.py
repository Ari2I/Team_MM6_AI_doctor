from copy import deepcopy
import math

import pytest

from llm_rag.contracts import AnalysisRequest
from llm_rag.corpus import Corpus
from llm_rag.providers import DemoProvider
from tests.helpers import corpus_data, request_data


class FixedEmbedder:
    """Тестовые векторы с заранее известной близостью для проверки ранжирования."""

    model_id = "test-only-vectors"
    revision = "test-1"
    dimension = 2

    def __init__(self):
        self.document_batches = []
        self.queries = []

    def embed_documents(self, texts):
        self.document_batches.append(list(texts))
        return [[0.0, 1.0] if "10 демо-ед." in text else [1.0, 0.0] for text in texts]

    def embed_query(self, text):
        self.queries.append(text)
        return [0.0, 1.0]


def test_cosine_index_normalizes_vectors_instead_of_ranking_by_magnitude():
    from llm_rag.vector_index import VectorIndex
    index = VectorIndex(["semantic", "large", "opposite"], [[1, 1], [100, 0], [-1, -1]])
    scores = index.similarities([2, 2])
    assert scores["semantic"] == pytest.approx(1)
    assert scores["large"] == pytest.approx(math.sqrt(0.5))
    assert scores["opposite"] == pytest.approx(-1)


@pytest.mark.parametrize("vectors", [[], [[]], [[0, 0]], [[1, float("nan")]], [[1, float("inf")]], [[True, 1]], [["1", 0]], [[1, 0], [1]]])
def test_vector_index_rejects_unusable_vectors(vectors):
    from llm_rag.vector_index import VectorIndex
    with pytest.raises(ValueError):
        VectorIndex([f"f-{i}" for i in range(len(vectors))], vectors)


def test_index_rejects_inconsistent_ids_and_query_dimensions():
    from llm_rag.vector_index import VectorIndex
    for ids, vectors in [(["same", "same"], [[1, 0], [0, 1]]), (["a", "b"], [[1, 0]])]:
        with pytest.raises(ValueError):
            VectorIndex(ids, vectors)
    index = VectorIndex(["a"], [[1, 0]])
    with pytest.raises(ValueError):
        index.similarities([1])


def test_semantic_similarity_changes_order_without_lexical_overlap():
    from llm_rag.retrieval import Retriever
    data = request_data()
    data["items"][0]["source_refs"] = []
    request = AnalysisRequest.model_validate(data)
    embedder = FixedEmbedder()
    retriever = Retriever(Corpus.model_validate(corpus_data()), embedder=embedder)
    results = retriever.search(request.items[0], request.diagnosis)
    assert [fragment.fragment_id for fragment in results] == ["dose-a", "master-a"]
    assert len(embedder.document_batches) == 1
    assert len(embedder.document_batches[0]) == 5
    assert len(embedder.queries) == 1
    assert "демо-вещество-а" in embedder.queries[0]
    assert "125.50" not in embedder.queries[0]


def test_explicit_reference_keeps_priority_before_vector_similarity():
    from llm_rag.retrieval import Retriever
    request = AnalysisRequest.model_validate(request_data())
    retriever = Retriever(Corpus.model_validate(corpus_data()), embedder=FixedEmbedder())
    assert [f.fragment_id for f in retriever.search(request.items[0], request.diagnosis)] == ["master-a", "dose-a"]


def test_vector_similarity_cannot_override_inn_or_diagnosis():
    from llm_rag.retrieval import Retriever
    data = request_data()
    data["diagnosis"] = "другой диагноз"
    request = AnalysisRequest.model_validate(data)
    embedder = FixedEmbedder()
    retriever = Retriever(Corpus.model_validate(corpus_data()), embedder=embedder)
    assert retriever.search(request.items[0], request.diagnosis) == []
    assert embedder.queries == []


def test_low_vector_score_does_not_hide_a_conflicting_source():
    from llm_rag.pipeline import Pipeline
    corpus = corpus_data()
    conflict = deepcopy(corpus["fragments"][0])
    conflict["fragment_id"] = "low-score-conflict"
    assertion = conflict["assertions"][0]
    assertion.update(claim_id="conflict", category="Б")
    for key in ["text", "quote"]:
        assertion[key] = assertion[key].replace("категории А", "категории Б")
    conflict["text"] = conflict["text"].replace("категории А", "категории Б")
    corpus["fragments"].append(conflict)
    result = Pipeline(Corpus.model_validate(corpus), DemoProvider(), embedder=FixedEmbedder()).run(
        AnalysisRequest.model_validate(request_data())
    )
    assert result.items[0].explanation_status == "SOURCE_CONFLICT"
    assert result.items[0].category == "А"
    assert "low-score-conflict" in {s.fragment_id for s in result.items[0].review_sources}
    assert result.retrieval_info.method == "vector"
    assert result.retrieval_info.embedding_model == "test-only-vectors"
    assert result.retrieval_info.embedding_dimension == 2
    assert result.retrieval_info.embedding_revision == "test-1"


def test_corpus_vectors_are_built_once_and_queries_are_encoded_per_item():
    from llm_rag.pipeline import Pipeline
    embedder = FixedEmbedder()
    pipeline = Pipeline(Corpus.model_validate(corpus_data()), DemoProvider(), embedder=embedder)
    request = AnalysisRequest.model_validate(request_data())
    first = pipeline.run(request)
    second = pipeline.run(request)
    assert len(embedder.document_batches) == 1
    assert len(embedder.queries) == 2
    assert first.retrieval_info.corpus_fingerprint == second.retrieval_info.corpus_fingerprint


def test_changed_corpus_text_changes_index_fingerprint_even_with_same_version():
    from llm_rag.retrieval import Retriever
    original = corpus_data()
    changed = deepcopy(original)
    changed["fragments"][0]["text"] += " Дополнительный синтетический текст."
    a = Retriever(Corpus.model_validate(original), embedder=FixedEmbedder())
    b = Retriever(Corpus.model_validate(changed), embedder=FixedEmbedder())
    assert a.info.corpus_fingerprint != b.info.corpus_fingerprint


def test_embedder_dimension_metadata_cannot_disagree_with_real_vectors():
    from llm_rag.retrieval import Retriever
    embedder = FixedEmbedder()
    embedder.dimension = 384
    with pytest.raises(ValueError):
        Retriever(Corpus.model_validate(corpus_data()), embedder=embedder)


def test_lexical_mode_reports_that_no_embedding_model_was_used():
    from llm_rag.pipeline import Pipeline
    result = Pipeline(Corpus.model_validate(corpus_data()), DemoProvider()).run(
        AnalysisRequest.model_validate(request_data())
    )
    assert result.retrieval_info.method == "lexical"
    assert result.retrieval_info.embedding_model is None


def test_empty_embedding_batch_does_not_load_optional_libraries():
    from llm_rag.embeddings import LocalMultilingualEmbedder
    embedder = LocalMultilingualEmbedder()
    assert embedder.embed_documents([]) == []


@pytest.mark.parametrize("kwargs", [{"batch_size": 0}, {"batch_size": True}, {"batch_size": -2}])
def test_local_embedder_rejects_invalid_batch_configuration(kwargs):
    from llm_rag.embeddings import LocalMultilingualEmbedder
    with pytest.raises(ValueError):
        LocalMultilingualEmbedder(**kwargs)
