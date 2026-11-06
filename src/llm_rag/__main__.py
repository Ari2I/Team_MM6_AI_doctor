"""Командный интерфейс для локального демо и явно включаемых API-провайдеров."""

import argparse
import json
from pathlib import Path
import sys

from pydantic import ValidationError

from llm_rag.application import analyze_files
from llm_rag.contracts import AnalysisRequest, AnalysisResult
from llm_rag.corpus import Corpus
from llm_rag.embeddings import EmbeddingError
from llm_rag.validation import GeneratedResponse


def exported_schemas() -> dict[str, dict]:
    models = {"input": AnalysisRequest, "output": AnalysisResult, "corpus": Corpus, "generation": GeneratedResponse}
    return {name: {"$schema": "https://json-schema.org/draft/2020-12/schema",
                   **model.model_json_schema(mode="serialization" if name == "output" else "validation")}
            for name, model in models.items()}


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="LLM/RAG: только синтетические демонстрационные данные")
    parser.add_argument("--request", type=Path, default=Path("examples/request.json"))
    parser.add_argument("--corpus", type=Path, default=Path("examples/corpus.json"))
    parser.add_argument("--output", type=Path, help="Файл результата JSON; по умолчанию вывод в терминал")
    parser.add_argument("--provider", choices=["demo", "gateway", "gigachat"], default="demo")
    parser.add_argument("--enable-api", action="store_true", help="Разрешить отправку демонстрационного контекста в выбранный API")
    parser.add_argument("--env-file", type=Path, help="Файл настроек; по умолчанию .env в корне проекта")
    parser.add_argument("--endpoint", help="HTTPS-адрес шлюза с протоколом из README")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--export-schemas", type=Path, metavar="DIRECTORY")
    parser.add_argument("--retrieval", choices=["lexical", "vector"], default="lexical", help="Способ ранжирования фрагментов")
    parser.add_argument("--download-embeddings", action="store_true", help="Разрешить первоначальное скачивание бесплатной модели")
    parser.add_argument("--embedding-cache", type=Path, help="Папка кэша модели эмбеддингов")
    args = parser.parse_args(argv)
    try:
        if args.export_schemas:
            args.export_schemas.mkdir(parents=True, exist_ok=True)
            for name, schema in exported_schemas().items():
                (args.export_schemas / f"{name}.schema.json").write_text(
                    json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
                )
            return 0
        if args.output and args.output.resolve() in {args.request.resolve(), args.corpus.resolve()}:
            raise ValueError("Результат не должен перезаписывать входные файлы")
        result = analyze_files(
            args.request, args.corpus, provider_name=args.provider, retrieval=args.retrieval,
            enable_api=args.enable_api, timeout=args.timeout, endpoint=args.endpoint or "",
            download_embeddings=args.download_embeddings, embedding_cache=args.embedding_cache,
            env_file=args.env_file,
        )
        output = result.model_dump_json(indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(output, encoding="utf-8")
        else:
            sys.stdout.write(output)
        return 0
    except EmbeddingError as error:
        print(f"Ошибка эмбеддингов: {error}", file=sys.stderr)
    except ValidationError:
        print("Ошибка контракта: проверьте вход и корпус по опубликованным JSON-схемам.", file=sys.stderr)
    except (OSError, ValueError, UnicodeError):
        print("Ошибка чтения/записи JSON или конфигурации. Для API нужен --enable-api. "
              "GigaChat: GIGACHAT_CREDENTIALS либо GIGACHAT_ACCESS_TOKEN. "
              "Gateway: HTTPS --endpoint и LLM_RAG_API_KEY.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
