"""Эмбеддинги только через GigaChat; кэш хранит проверенные публичные векторы."""

from hashlib import sha256
import json
import math
import os
from pathlib import Path
from typing import Protocol
from uuid import uuid4

DEFAULT_MAX_TEXT_BYTES = 4000


class EmbeddingError(ValueError):
    """Безопасная ошибка вычисления/проверки эмбеддингов."""


class Embedder(Protocol):
    model_id: str
    revision: str
    dimension: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


class GigaChatEmbedder:
    """Ленивый API: только допущенный корпус и точные публичные поисковые строки."""

    def __init__(
        self,
        *,
        model_id="EmbeddingsGigaR",
        dimension=2560,
        revision=None,
        client_factory=None,
        allow_network=False,
        cache_path=None,
        batch_size=8,
        allowed_document_hashes=(),
        allowed_queries=(),
        max_text_bytes=DEFAULT_MAX_TEXT_BYTES,
        before_call=None,
    ):
        if any(type(v) is not int or v <= 0 for v in (dimension, batch_size, max_text_bytes)):
            raise EmbeddingError("Размерность, пакет и предел текста должны быть положительными целыми.")
        if not isinstance(model_id, str) or not model_id.strip() or type(allow_network) is not bool:
            raise EmbeddingError("Некорректная модель или разрешение сети.")
        self.model_id = model_id
        self.dimension = dimension
        self.revision = revision or "unverified:" + model_id
        self._expected_revision = revision
        self._factory = client_factory
        self._network = allow_network
        self._cache_path = Path(cache_path) if cache_path else None
        self._batch = batch_size
        self._max_bytes = max_text_bytes
        self._documents = frozenset(allowed_document_hashes)
        self._queries = frozenset(allowed_queries)
        self._before_call = before_call
        self._cache = {}
        self.calls = 0
        self.metadata_calls = 0
        self.tokens = 0
        if self._cache_path and self._cache_path.exists():
            try:
                value = json.loads(self._cache_path.read_text(encoding="utf-8"))
                if (
                    value["schema_version"] != "gigachat-vectors.1"
                    or value["model"] != model_id
                    or value["dimension"] != dimension
                    or (revision is not None and value["revision"] != revision)
                ):
                    raise ValueError("Версия кэша не совпадает")
                self.revision = value["revision"]
                self._cache = {key: self._vector(vector) for key, vector in value["vectors"].items()}
            except (KeyError, ValueError, TypeError):
                raise EmbeddingError("Кэш эмбеддингов повреждён или несовместим.") from None

    @property
    def max_text_bytes(self):
        """Public input bound; retrieval may keep longer evidence unranked."""
        return self._max_bytes

    def _vector(self, value):
        if (
            not isinstance(value, list)
            or len(value) != self.dimension
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in value)
            or not any(value)
        ):
            raise EmbeddingError("API вернул некорректную размерность или значения вектора.")
        return list(value)

    def _invoke(self, client, method, *args, reserved_tokens=0, **kwargs):
        def invoke():
            if method == "embeddings":
                self.calls += 1
            elif method == "get_models":
                self.metadata_calls += 1
            return getattr(client, method)(*args, **kwargs)

        if self._before_call:
            remaining = self._before_call(reserved_tokens)
            if not callable(getattr(client, "request_limits", None)):
                raise EmbeddingError("Клиент эмбеддингов не поддерживает ограничение остатком времени.")
            with client.request_limits(timeout=remaining, max_tokens=0):
                return invoke()
        return invoke()

    def _encode(self, texts):
        if not texts:
            return []
        if any(not isinstance(t, str) or not t.strip() or len(t.encode()) > self._max_bytes for t in texts):
            raise EmbeddingError(
                "Пустой или слишком длинный фрагмент: подготовьте корпус без обрезки условий."
            )
        keys = [sha256(t.encode()).hexdigest() for t in texts]
        missing = list(dict.fromkeys(key for key in keys if key not in self._cache))
        prepared = dict(zip(keys, texts))
        staged = {}
        if missing:
            if not self._network or self._factory is None:
                raise EmbeddingError("Векторов нет в кэше; API эмбеддингов явно не разрешён.")
            try:
                with self._factory() as client:
                    models = self._invoke(client, "get_models")
                    if self.model_id not in {m.id_ for m in models.data}:
                        raise EmbeddingError(
                            "Выбранная модель отсутствует в списке доступных моделей проекта."
                        )
                    for offset in range(0, len(missing), self._batch):
                        group = missing[offset : offset + self._batch]
                        batch = [prepared[key] for key in group]
                        response = self._invoke(
                            client,
                            "embeddings",
                            batch,
                            model=self.model_id,
                            reserved_tokens=sum(len(t.encode()) + 64 for t in batch),
                        )
                        actual = response.model
                        if (
                            not actual
                            or (self._expected_revision and actual != self._expected_revision)
                            or (not self.revision.startswith("unverified:") and actual != self.revision)
                        ):
                            raise EmbeddingError("Модель/редакция API изменилась; требуется новый индекс.")
                        self.revision = actual
                        rows = response.data
                        indices = [row.index for row in rows]
                        if (
                            len(rows) != len(batch)
                            or set(indices) != set(range(len(batch)))
                            or any(type(i) is not int for i in indices)
                        ):
                            raise EmbeddingError("Количество или индексы векторов не соответствуют входу.")
                        for row in rows:
                            staged[group[row.index]] = self._vector(row.embedding)
                            usage = getattr(getattr(row, "usage", None), "prompt_tokens", None)
                            if type(usage) is int and usage >= 0:
                                self.tokens += usage
            except EmbeddingError:
                raise
            except Exception:
                raise EmbeddingError("Не удалось получить проверенные эмбеддинги GigaChat.") from None
        combined = self._cache | staged
        if self._cache_path and staged:
            combined = self._publish(combined)
        self._cache = combined
        return [list(combined[key]) for key in keys]

    def _publish(self, combined):
        from llm_rag.file_lock import exclusive_file_lock

        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self._cache_path.with_name(self._cache_path.name + ".lock")):
            if self._cache_path.exists():
                current = json.loads(self._cache_path.read_text(encoding="utf-8"))
                if (
                    current.get("schema_version") != "gigachat-vectors.1"
                    or current.get("model") != self.model_id
                    or current.get("revision") != self.revision
                    or current.get("dimension") != self.dimension
                ):
                    raise EmbeddingError("Другой процесс изменил модель/редакцию кэша.")
                latest = {key: self._vector(value) for key, value in current["vectors"].items()}
                combined = combined | latest
            value = dict(
                schema_version="gigachat-vectors.1",
                model=self.model_id,
                revision=self.revision,
                dimension=self.dimension,
                vectors=combined,
            )
            temporary = self._cache_path.with_name(self._cache_path.name + "." + uuid4().hex + ".tmp")
            try:
                temporary.write_text(json.dumps(value, allow_nan=False), encoding="utf-8")
                os.replace(temporary, self._cache_path)
            finally:
                temporary.unlink(missing_ok=True)
        return combined

    def embed_documents(self, texts):
        if any(
            not isinstance(t, str) or sha256(t.encode()).hexdigest() not in self._documents for t in texts
        ):
            raise EmbeddingError("Текст не входит в разрешённый публичный корпус.")
        return self._encode(texts)

    def embed_query(self, text):
        if text not in self._queries:
            raise EmbeddingError("Поисковый текст не входит в разрешённый набор медицинских терминов.")
        return self._encode([text])[0]


def corpus_embedder(
    corpus, settings, *, allow_network=False, cache_path=None, timeout=20.0, before_call=None, verify_ssl_certs=True
):
    """Собрать провайдер из явно переданного допущенного корпуса; заявки не участвуют."""
    texts = [f"МНН: {f.inn}. Диагноз: {f.diagnosis}. {f.text}" for f in corpus.fragments]
    queries = {f"МНН: {f.inn}. Диагноз: {f.diagnosis}." for f in corpus.fragments}

    def factory():
        from llm_rag.providers.gigachat import GigaChatProvider

        scope = settings.get("GIGACHAT_SCOPE") or "GIGACHAT_API_B2B"
        if scope not in {"GIGACHAT_API_B2B", "GIGACHAT_API_CORP"}:
            raise EmbeddingError("Эмбеддинги требуют корпоративного scope.")

        class Transport(GigaChatProvider):
            def get_models(self):
                return self._client.get_models()

            def embeddings(self, texts, model):
                return self._client.embeddings(texts, model=model)

        return Transport(
            credentials=settings.get("GIGACHAT_CREDENTIALS"),
            access_token=settings.get("GIGACHAT_ACCESS_TOKEN"),
            scope=scope,
            enabled=True,
            verify_ssl_certs=verify_ssl_certs,
            ca_bundle=settings.get("GIGACHAT_CA_BUNDLE_FILE"),
            timeout=timeout,
        )

    return GigaChatEmbedder(
        model_id=settings.get("GIGACHAT_EMBEDDING_MODEL") or "EmbeddingsGigaR",
        dimension=int(settings.get("GIGACHAT_EMBEDDING_DIMENSION") or "2560"),
        revision=settings.get("GIGACHAT_EMBEDDING_REVISION"),
        client_factory=factory,
        allow_network=allow_network,
        cache_path=cache_path,
        before_call=before_call,
        allowed_document_hashes={sha256(t.encode()).hexdigest() for t in texts},
        allowed_queries=queries,
    )
