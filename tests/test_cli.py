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
        [sys.executable, "-m", "llm_rag", *map(str, args)],
        cwd=cwd or ROOT, env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, capture_output=True, text=True, encoding="utf-8", timeout=20,
    )


@pytest.mark.parametrize("name", ["input", "output", "corpus", "generation",'integration_input','integration_output'])
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


@pytest.mark.parametrize("name", ["integration_input", "integration_output"])
def test_public_schemas_allow_only_optional_adult_or_child_age_group(name):
    conditions_schema = exported_schemas()[name]["$defs"]["LocalConditions"]
    for conditions in ({}, {"age_group": None}, {"age_group": "adult"}, {"age_group": "child"}):
        jsonschema.validate(conditions, conditions_schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"age_group": "other"}, conditions_schema)


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
