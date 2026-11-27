"""Ожидания публичного примера заданы отдельно от результата выполнения."""

import json
from tests.helpers import ROOT


def test_public_example_matches_predefined_properties():
    folder = ROOT / "examples/integration"
    expected = json.loads((folder / "expected-properties.json").read_text(encoding="utf-8"))
    from llm_rag.integration import _run, IntegrationResources
    from llm_rag.integration_contracts import IntegrationRequest
    from llm_rag.ml2.registry import RegistrySnapshot
    result = _run(IntegrationRequest.model_validate_json((folder / "request.json").read_bytes()),
                  resources=IntegrationResources(registry=RegistrySnapshot.load(ROOT / "examples/ml2/registry.synthetic.json")))
    assert len(result.pairs) == expected["pair_count"]
    assert len(result.unprocessed_pairs) == expected["unprocessed_count"]
    assert {key: str(amount) for key, amount in result.costs.totals.items()} == expected["known_totals"]
    assert sum("INN_UNRESOLVED" in pair.reasons for pair in result.pairs) == expected["inn_unresolved_count"]
    assert all(pair.decision.requires_review == expected["requires_review"] for pair in result.pairs)
