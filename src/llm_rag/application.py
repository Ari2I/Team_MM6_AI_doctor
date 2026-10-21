"""Общая функция запуска анализа из командной строки и локального меню."""

import json
import os
from pathlib import Path

from dotenv import dotenv_values

from llm_rag.contracts import AnalysisRequest, AnalysisResult
from llm_rag.corpus import Corpus
from llm_rag.embeddings import EmbeddingError, LocalMultilingualEmbedder
from llm_rag.pipeline import Pipeline
from llm_rag.providers import DemoProvider, JsonApiProvider
from llm_rag.providers.gigachat import GigaChatProvider


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Повторяющийся ключ JSON")
        result[key] = value
    return result


def read_json(path: Path) -> dict:
    """Прочитать JSON с проверкой повторяющихся ключей."""
    return json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_keys)


def analyze_files(
    request_path: Path, corpus_path: Path, *, provider_name: str = "demo",
    retrieval: str = "lexical", enable_api: bool = False, timeout: float = 20.0,
    endpoint: str = "", download_embeddings: bool = False,
    embedding_cache: Path | None = None, env_file: Path | None = None,
    verify_ssl_certs: bool = True,
) -> AnalysisResult:
    """Проверить файлы и вызвать Pipeline.run; переменные окружения имеют приоритет над .env."""
    if provider_name not in {"demo", "gateway", "gigachat"}:
        raise ValueError("Неизвестный провайдер")
    if provider_name != "demo" and not enable_api:
        raise ValueError("Для внешнего провайдера необходимо явно включить API")
    if retrieval not in {"lexical", "vector"}:
        raise ValueError("Неизвестный способ поиска")
    if retrieval != "vector" and (download_embeddings or embedding_cache is not None):
        raise EmbeddingError("Для настроек эмбеддингов выберите векторный поиск: --retrieval vector")
    request = AnalysisRequest.model_validate(read_json(request_path))
    corpus = Corpus.model_validate(read_json(corpus_path))
    # Читаем настройки заново при каждом запуске меню, не изменяя окружение процесса.
    settings = {
        **dotenv_values(env_file if env_file is not None else PROJECT_ROOT / ".env",
                        encoding="utf-8-sig", interpolate=False),
        **os.environ,
    }
    provider = DemoProvider()
    if provider_name == "gateway":
        provider = JsonApiProvider(
            endpoint, settings.get("LLM_RAG_API_KEY") or "", enabled=enable_api, timeout=timeout,
        )
    elif provider_name == "gigachat":
        provider = GigaChatProvider(
            credentials=settings.get("GIGACHAT_CREDENTIALS"),
            access_token=settings.get("GIGACHAT_ACCESS_TOKEN"),
            model=settings.get("GIGACHAT_MODEL") or "GigaChat-2",
            scope=settings.get("GIGACHAT_SCOPE") or "GIGACHAT_API_PERS",
            ca_bundle=settings.get("GIGACHAT_CA_BUNDLE_FILE"),
            enabled=enable_api, timeout=timeout,
            verify_ssl_certs=verify_ssl_certs,
        )
    try:
        embedder = LocalMultilingualEmbedder(
            allow_download=download_embeddings, cache_dir=embedding_cache,
        ) if retrieval == "vector" else None
        return Pipeline(corpus, provider, embedder=embedder).run(request)
    finally:
        if isinstance(provider, GigaChatProvider):
            provider.close()
