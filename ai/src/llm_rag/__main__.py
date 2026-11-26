"""Командный интерфейс для локальной обработки и явно включаемых API-провайдеров."""

import argparse
import json
from pathlib import Path


from llm_rag.contracts import AnalysisRequest, AnalysisResult
from llm_rag.corpus import Corpus
from llm_rag.validation import GeneratedResponse
from llm_rag.integration_contracts import IntegrationRequest, IntegrationResult


def exported_schemas() -> dict[str, dict]:
    models = {"input": AnalysisRequest, "output": AnalysisResult, "corpus": Corpus, "generation": GeneratedResponse,
              'integration_input':IntegrationRequest,'integration_output':IntegrationResult}
    return {name: {"$schema": "https://json-schema.org/draft/2020-12/schema",
                   **model.model_json_schema(mode="serialization" if name in {'output','integration_output'} else "validation")}
            for name, model in models.items()}


def main(argv=None):
    """Выгрузить схемы контрактов установленного пакета."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export-schemas", required=True, type=Path)
    folder = parser.parse_args(argv).export_schemas
    folder.mkdir(parents=True, exist_ok=True)
    for name, schema in exported_schemas().items():
        (folder / f"{name}.schema.json").write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
