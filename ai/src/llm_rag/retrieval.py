"""Детерминированный индекс в памяти. Ранжирование само по себе не доказывает применимость."""

import re
import hashlib
from collections import defaultdict

from llm_rag.contracts import AnalysisItem, RetrievalInfo
from llm_rag.corpus import Corpus, Fragment, normalize
from llm_rag.embeddings import Embedder
from llm_rag.vector_index import VectorIndex


class Retriever:
    def __init__(self, corpus: Corpus, *, embedder: Embedder | None = None):
        self._index: dict[tuple[str, str], list[Fragment]] = defaultdict(list)
        for fragment in corpus.fragments:
            self._index[(normalize(fragment.inn), normalize(fragment.diagnosis))].append(fragment)
        self._embedder = embedder
        self._vectors = None
        if embedder is not None:
            texts = [f"МНН: {f.inn}. Диагноз: {f.diagnosis}. {f.text}" for f in corpus.fragments]
            self._vectors = VectorIndex([f.fragment_id for f in corpus.fragments], embedder.embed_documents(texts))
            if self._vectors.dimension != embedder.dimension:
                raise ValueError("Размерность векторов не совпадает с описанием модели")
        self.info = RetrievalInfo(
            method="vector" if embedder else "lexical",
            embedding_model=embedder.model_id if embedder else None,
            embedding_revision=embedder.revision if embedder else None,
            embedding_dimension=embedder.dimension if embedder else None,
            corpus_fingerprint=hashlib.sha256(corpus.model_dump_json().encode("utf-8")).hexdigest(),
        )

    def search(self, item: AnalysisItem, diagnosis: str) -> list[Fragment]:
        if item.inn is None:
            return []
        candidates = self._index.get((normalize(item.inn), normalize(diagnosis)), [])
        if not candidates:
            return []
        query = set(re.findall(r"\w+", normalize(item.classification_reason or "")))
        refs = set(item.source_refs)
        semantic = {}
        if self._vectors is not None:
            text = (f"МНН: {item.inn}. Диагноз: {diagnosis}. "
                    f"{item.classification_reason or ''} Дозировка: {item.original.dosage}. "
                    f"{item.dosage_check.reason or ''}")
            semantic = self._vectors.similarities(self._embedder.embed_query(text))

        def rank(fragment):
            explicit = fragment.source_id in refs or fragment.fragment_id in refs
            overlap = len(query & set(re.findall(r"\w+", normalize(fragment.text))))
            return (-int(explicit), -semantic.get(fragment.fragment_id, 0.0), -overlap, fragment.fragment_id)

        # Не ограничиваем выдачу первыми совпадениями, чтобы не скрыть конфликт источников.
        return sorted(candidates, key=rank)
