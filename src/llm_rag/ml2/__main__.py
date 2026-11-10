"""Запуск ML-2 отдельно от CLI коллеги: python -m llm_rag.ml2 --help."""

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import dotenv_values
from pydantic import ValidationError

from llm_rag.application import read_json
from llm_rag.contracts import AnalysisRequest
from llm_rag.corpus import Corpus

from .classifier import EvidenceFragment, gigachat_completer
from .pipeline import ClinicalSources, ML2Pipeline
from .registry import RegistrySnapshot


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--request", required=True, type=Path, help="Синтетический AnalysisRequest 1.0"
    )
    parser.add_argument(
        "--registry", required=True, type=Path, help="Датированный JSON-снимок ГРЛС"
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="AnalysisRequest для существующего RAG",
    )
    parser.add_argument(
        "--details",
        type=Path,
        help="Отдельный JSON со статусами ML-2 и полными цитатами",
    )
    parser.add_argument(
        "--approved-corpus",
        type=Path,
        help="Явно согласованные правила в формате Corpus",
    )
    parser.add_argument(
        "--fragments",
        type=Path,
        help="JSON: МНН -> список EvidenceFragment для offline-обработки",
    )
    parser.add_argument(
        "--icd10", help="Код МКБ-10; иначе context.icd10 или начало diagnosis"
    )
    parser.add_argument(
        "--live-sources",
        action="store_true",
        help="Разрешить публичный каталог и карточки КР",
    )
    parser.add_argument("--cache", type=Path)
    parser.add_argument(
        "--enable-api",
        action="store_true",
        help="Разрешить классификацию через GigaChat",
    )
    parser.add_argument(
        "--env-file", type=Path, help="Настройки; секреты в результаты не записываются"
    )
    args = parser.parse_args(argv)
    provider = None
    try:
        inputs = [
            args.request,
            args.registry,
            args.approved_corpus,
            args.fragments,
            args.env_file,
        ]
        outputs = [path.resolve() for path in (args.output, args.details) if path]
        if len(outputs) != len(set(outputs)) or any(
            p.resolve() in outputs for p in inputs if p
        ):
            raise ValueError(
                "Выходные файлы должны отличаться от входных и друг от друга"
            )
        request = AnalysisRequest.model_validate(read_json(args.request))
        registry = RegistrySnapshot.load(args.registry)
        approved = (
            Corpus.model_validate(read_json(args.approved_corpus))
            if args.approved_corpus
            else None
        )
        fragments = None
        if args.fragments:
            raw = read_json(args.fragments)
            fragments = {
                inn: tuple(EvidenceFragment(**row) for row in rows)
                for inn, rows in raw.items()
            }
        complete = None
        if args.enable_api:
            from .provider import ML2GigaChatProvider

            env_file = args.env_file or Path(__file__).resolve().parents[3] / ".env"
            settings = {
                **dotenv_values(env_file, encoding="utf-8-sig", interpolate=False),
                **os.environ,
            }
            ca_key = (
                "GIGACHAT_CA_BUNDLE_FILE"
                if settings.get("GIGACHAT_CA_BUNDLE_FILE")
                else "GIGACHAT_CA_BUNDLE"
            )
            ca_bundle = settings.get(ca_key)
            if (
                ca_bundle
                and not Path(ca_bundle).is_absolute()
                and ca_key not in os.environ
            ):
                ca_bundle = str((env_file.resolve().parent / ca_bundle).resolve())
            provider = ML2GigaChatProvider(
                credentials=settings.get("GIGACHAT_CREDENTIALS"),
                access_token=settings.get("GIGACHAT_ACCESS_TOKEN"),
                enabled=True,
                model=settings.get("GIGACHAT_MODEL") or "GigaChat-2",
                scope=settings.get("GIGACHAT_SCOPE") or "GIGACHAT_API_B2B",
                ca_bundle=ca_bundle,
            )
            complete = gigachat_completer(provider)
        kwargs = {"cache": args.cache} if args.cache else {}
        batch = ML2Pipeline(
            registry,
            complete_json=complete,
            approved_corpus=approved,
            fragments_by_inn=fragments,
            sources=ClinicalSources(**kwargs),
        ).run(request, diagnosis_code=args.icd10, live_sources=args.live_sources)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            batch.request.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        if args.details:
            args.details.parent.mkdir(parents=True, exist_ok=True)
            args.details.write_text(
                json.dumps(batch.details(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        print(f"ML-2: {len(batch.evaluations)} строк; вход для RAG: {args.output}")
        return 0
    except (OSError, ValueError, TypeError, ValidationError, ImportError):
        print(
            "ML-2: проверьте JSON, пути, настройки модели и зависимости. Результат не завершён.",
            file=sys.stderr,
        )
        return 2
    finally:
        if provider is not None:
            provider.close()


if __name__ == "__main__":
    raise SystemExit(main())
