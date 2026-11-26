"""Эмбеддинги API: публичный набор, целостность ответа и атомарность кэша."""

from hashlib import sha256
from types import SimpleNamespace as Obj
import pytest


class Client:
    def __init__(self):
        self.closed = False
        self.calls = 0
        self.bad = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    def get_models(self):
        from gigachat.models.models import Models

        return Models.model_validate(
            {"data": [{"id": "EmbeddingsGigaR", "object": "model", "owned_by": "test"}], "object": "list"}
        )

    def embeddings(self, texts, model):
        self.calls += 1
        rows = [
            Obj(index=i, embedding=[float(i + 1), 1.0], usage=Obj(prompt_tokens=2))
            for i, _ in enumerate(texts)
        ]
        if self.bad == "count":
            rows.pop()
        if self.bad == "index":
            rows[0].index = 100
        if self.bad == "nan":
            rows[0].embedding = [float("nan"), 1.0]
        if self.bad == "dimension":
            rows[0].embedding = [1.0]
        return Obj(data=list(reversed(rows)), model="EmbeddingsGigaR")


def embedder(client, **kwargs):
    from llm_rag.embeddings import GigaChatEmbedder

    return GigaChatEmbedder(
        client_factory=lambda: client,
        allow_network=True,
        dimension=2,
        allowed_document_hashes={sha256(t.encode()).hexdigest() for t in ["Источник один", "Источник два"]},
        allowed_queries={"МНН: Вещество. Диагноз: Диагноз."},
        **kwargs,
    )


def test_api_embeddings_restore_input_order_and_close_client(tmp_path):
    client = Client()
    value = embedder(client, cache_path=tmp_path / "vectors.json")
    assert value.embed_documents(["Источник один", "Источник два"]) == [[1.0, 1.0], [2.0, 1.0]]
    assert client.closed and client.calls == 1
    assert value.dimension == 2 and value.revision == "EmbeddingsGigaR"
    assert value.embed_documents(["Источник один"]) == [[1.0, 1.0]]
    assert client.calls == 1


@pytest.mark.parametrize("bad", ["count", "index", "nan", "dimension"])
def test_bad_response_does_not_publish_cache(bad, tmp_path):
    from llm_rag.embeddings import EmbeddingError

    client = Client()
    client.bad = bad
    path = tmp_path / "vectors.json"
    with pytest.raises(EmbeddingError):
        embedder(client, cache_path=path).embed_documents(["Источник один"])
    assert not path.exists() and client.closed


def test_unapproved_document_and_query_never_open_client():
    from llm_rag.embeddings import EmbeddingError

    client = Client()
    value = embedder(client)
    with pytest.raises(EmbeddingError):
        value.embed_query("PRIVATE request 123")
    with pytest.raises(EmbeddingError):
        value.embed_documents(["PRIVATE"])
    assert client.calls == 0


def test_cached_vectors_are_available_without_network(tmp_path):
    from llm_rag.embeddings import GigaChatEmbedder

    path = tmp_path / "vectors.json"
    embedder(Client(), cache_path=path).embed_documents(["Источник один"])
    value = GigaChatEmbedder(
        dimension=2, cache_path=path, allowed_document_hashes={sha256("Источник один".encode()).hexdigest()}
    )
    assert value.embed_documents(["Источник один"]) == [[1.0, 1.0]]


def test_stale_instances_merge_cache_without_losing_other_vectors(tmp_path):
    from llm_rag.embeddings import GigaChatEmbedder

    path = tmp_path / "shared.json"
    first = embedder(Client(), cache_path=path)
    second = embedder(Client(), cache_path=path)
    first.embed_documents(["Источник один"])
    second.embed_documents(["Источник два"])
    offline = GigaChatEmbedder(
        dimension=2,
        cache_path=path,
        allowed_document_hashes={sha256(t.encode()).hexdigest() for t in ["Источник один", "Источник два"]},
    )
    assert len(offline.embed_documents(["Источник один", "Источник два"])) == 2
