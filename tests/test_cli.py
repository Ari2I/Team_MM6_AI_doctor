import json
import os
import subprocess
import sys

import jsonschema
import pytest

from tests.helpers import ROOT, request_data
from llm_rag.__main__ import exported_schemas


def cli(*args, cwd=None, env=None):
    return subprocess.run(
        [sys.executable, str(ROOT / "run.py"), "--env-file", os.devnull, *map(str, args)],
        cwd=cwd or ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=20,
    )


def test_cli_runs_actual_pipeline_and_output_matches_published_schema(tmp_path):
    output = tmp_path / "result.json"
    process = cli("--request", ROOT / "examples/request.json", "--corpus", ROOT / "examples/corpus.json", "--output", output, cwd=tmp_path)
    assert process.returncode == 0, process.stderr
    data = json.loads(output.read_text(encoding="utf-8"))
    schema = exported_schemas()["output"]
    jsonschema.validate(data, schema)
    assert data["completeness"] == "PARTIAL"
    assert len(data["items"]) == 8
    assert data["costs_by_currency"][0]["total_amount"] == "355.00"
    assert "Демо" in output.read_text(encoding="utf-8")


def test_cli_emits_utf8_json_on_stdout():
    process = cli()
    assert process.returncode == 0, process.stderr
    data = json.loads(process.stdout)
    assert data["provider_name"] == "offline-extractive-demo"


def test_cli_invalid_input_is_nonzero_and_does_not_echo_values(tmp_path):
    data = request_data()
    data["items"][0]["original"]["amount"] = "secret-patient-value"
    source = tmp_path / "bad.json"
    source.write_text(json.dumps(data), encoding="utf-8")
    output = tmp_path / "result.json"
    process = cli("--request", source, "--output", output)
    assert process.returncode == 2
    assert not output.exists()
    assert "secret-patient-value" not in process.stderr
    assert "Traceback" not in process.stderr


def test_cli_rejects_ambiguous_duplicate_json_keys(tmp_path):
    source = tmp_path / "bad.json"
    source.write_text('{"is_demo":false,"is_demo":true}', encoding="utf-8")
    process = cli("--request", source)
    assert process.returncode == 2


def test_cli_gateway_requires_explicit_enablement_and_configured_key():
    env = os.environ.copy()
    env.pop("LLM_RAG_API_KEY", None)
    process = cli("--provider", "gateway", "--endpoint", "https://gateway.example", env=env)
    assert process.returncode == 2
    assert "Traceback" not in process.stderr


def test_cli_gigachat_is_available_and_requires_local_credentials():
    env = os.environ.copy()
    env.pop("GIGACHAT_CREDENTIALS", None)
    env.pop("GIGACHAT_ACCESS_TOKEN", None)
    process = cli("--provider", "gigachat", "--enable-api", env=env)
    assert process.returncode == 2
    assert "invalid choice" not in process.stderr
    assert "GIGACHAT_CREDENTIALS" in process.stderr
    assert "Traceback" not in process.stderr


def test_cli_exposes_vector_mode_and_explicit_download_switch():
    process = cli("--help")
    assert process.returncode == 0
    assert "--retrieval" in process.stdout
    assert "--download-embeddings" in process.stdout


def test_cli_does_not_ignore_embedding_flags_in_lexical_mode():
    process = cli("--retrieval", "lexical", "--download-embeddings")
    assert process.returncode == 2
    assert "векторный" in process.stderr.lower()


def test_cli_vector_mode_with_empty_local_cache_returns_actionable_error(tmp_path):
    process = cli("--retrieval", "vector", "--embedding-cache", tmp_path / "empty-model-cache")
    assert process.returncode == 2
    assert "эмбеддинг" in process.stderr.lower()
    assert "Traceback" not in process.stderr


@pytest.mark.parametrize("name", ["input", "output", "corpus", "generation"])
def test_cli_exports_valid_schemas_without_committed_generated_files(name, tmp_path):
    destination = tmp_path / "schemas"
    process = cli("--export-schemas", destination)
    assert process.returncode == 0, process.stderr
    actual = json.loads((destination / f"{name}.schema.json").read_text(encoding="utf-8"))
    assert actual == exported_schemas()[name]
    jsonschema.Draft202012Validator.check_schema(actual)


def test_demo_request_matches_published_input_schema():
    schema = exported_schemas()["input"]
    data = json.loads((ROOT / "examples/request.json").read_text(encoding="utf-8"))
    jsonschema.validate(data, schema)


@pytest.mark.parametrize("amount", [1.3, 1.0, 1e20, 1, 0, "-1", "NaN", "Infinity", True, "1_000", " 1 "])
def test_input_schema_and_runtime_both_reject_invalid_money(amount):
    from pydantic import ValidationError
    from llm_rag.contracts import AnalysisRequest
    schema = exported_schemas()["input"]
    data = request_data()
    data["items"][0]["original"]["amount"] = amount
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(data, schema)
    with pytest.raises(ValidationError):
        AnalysisRequest.model_validate(data)
