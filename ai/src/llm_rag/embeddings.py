"""Локальные бесплатные эмбеддинги на основе многоязычной модели MiniLM."""

from pathlib import Path
from typing import Protocol


MODEL_ID = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
MODEL_REVISION = "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"


class EmbeddingError(ValueError):
    """Ошибка загрузки модели или вычисления эмбеддингов без раскрытия входных данных."""


class Embedder(Protocol):
    model_id: str
    revision: str
    dimension: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class LocalMultilingualEmbedder:
    """Вычисление нормализованных векторов на CPU без внешнего API."""

    model_id = MODEL_ID
    revision = MODEL_REVISION
    dimension = 384

    def __init__(self, *, batch_size: int = 8, allow_download: bool = False, cache_dir: Path | None = None):
        if type(batch_size) is not int or batch_size <= 0:
            raise ValueError("Размер пакета эмбеддингов должен быть положительным целым числом")
        self._batch_size = batch_size
        self._allow_download = allow_download
        self._cache_dir = cache_dir
        self._tokenizer = None
        self._model = None

    def _load(self):
        if self._model is not None:
            return
        try:
            from huggingface_hub import snapshot_download
            # Сначала находим закреплённый снимок, затем загружаем его как локальную папку.
            # Это исключает дополнительные сетевые проверки внутри токенизатора Transformers.
            snapshot = snapshot_download(
                self.model_id, revision=self.revision, local_files_only=not self._allow_download,
                token=False, cache_dir=str(self._cache_dir) if self._cache_dir is not None else None,
                allow_patterns=["config.json", "tokenizer.json", "tokenizer_config.json",
                                "special_tokens_map.json", "model.safetensors"],
            )
            from transformers import AutoModel, AutoTokenizer
            options = {
                "local_files_only": True,
                "trust_remote_code": False, "token": False,
            }
            tokenizer = AutoTokenizer.from_pretrained(snapshot, **options)
            model = AutoModel.from_pretrained(snapshot, use_safetensors=True, **options)
            model.to("cpu")
            model.eval()
            self._tokenizer, self._model = tokenizer, model
        except ImportError:
            raise EmbeddingError("Установите зависимости: python -m pip install -r requirements.txt") from None
        except Exception:
            raise EmbeddingError(
                "Не удалось загрузить локальную модель эмбеддингов. Проверьте кэш; "
                "для первоначального скачивания укажите --download-embeddings."
            ) from None

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise EmbeddingError("Текст для эмбеддинга должен быть непустой строкой")
        self._load()
        try:
            import torch
            vectors = []
            for offset in range(0, len(texts), self._batch_size):
                encoded = self._tokenizer(
                    texts[offset:offset + self._batch_size], padding=True, truncation=True,
                    max_length=128, return_tensors="pt",
                )
                with torch.inference_mode():
                    hidden = self._model(**encoded).last_hidden_state
                    # Усредняем только реальные токены, исключая заполнение коротких строк.
                    mask = encoded["attention_mask"].unsqueeze(-1).to(hidden.dtype)
                    pooled = (hidden * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1e-9)
                    normalized = torch.nn.functional.normalize(pooled, p=2, dim=1)
                vectors.extend(normalized.cpu().tolist())
            return vectors
        except Exception:
            raise EmbeddingError("Не удалось вычислить локальные эмбеддинги") from None

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]
