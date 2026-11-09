import math
import os

import pytest


@pytest.mark.skipif(os.environ.get("LLM_RAG_TEST_LOCAL_EMBEDDINGS") != "1", reason="Реальная модель запускается явно отдельной проверкой")
def test_real_local_model_encodes_russian_semantics_without_api(monkeypatch):
    import requests
    def network_forbidden(*args, **kwargs):
        pytest.fail("Локальные эмбеддинги не должны обращаться к сети")
    monkeypatch.setattr(requests.sessions.Session, "request", network_forbidden)
    from llm_rag.embeddings import LocalMultilingualEmbedder
    from llm_rag.vector_index import VectorIndex
    embedder = LocalMultilingualEmbedder(batch_size=2)
    texts = ["Автомобиль припаркован у дома.", "Машина стоит возле здания.", "Кошка спит на диване."]
    vectors = embedder.embed_documents(texts)
    assert len(vectors) == 3
    assert all(len(vector) == 384 for vector in vectors)
    assert all(math.isclose(sum(x*x for x in vector), 1, abs_tol=1e-5) for vector in vectors)
    query = embedder.embed_query("Где оставили автомобиль?")
    scores = VectorIndex(["car", "vehicle", "cat"], vectors).similarities(query)
    assert scores["vehicle"] > scores["cat"]
    assert scores["car"] > scores["cat"]
