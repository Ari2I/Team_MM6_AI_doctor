"""Контроль отличает категории/модель/свежие векторы от HTTP-успеха с fallback."""

from copy import deepcopy
from hashlib import sha256

import pytest

from scripts.verify_category_acceptance import assess

pytestmark = pytest.mark.usefixtures("no_network")


def sample():
    doc = {
        "inn": "Синтетическое вещество",
        "diagnosis": "Синтетический диагноз",
        "text": "Синтетическая группа рекомендована для лечения заболевания.",
    }
    prefix = f"МНН: {doc['inn']}. Диагноз: {doc['diagnosis']}."
    key = sha256((prefix + " " + doc["text"]).encode()).hexdigest()
    info = {
        "method": "vector",
        "embedding_model": "test-only",
        "embedding_revision": "r1",
        "embedding_dimension": 2,
    }
    result = {
        "completeness": "COMPLETE",
        "unprocessed_pairs": [],
        "pairs": [
            {
                "generation_mode": "MODEL",
                "reasons": [],
                "citations": [{"quote": doc["text"]}],
                "classification_diagnostics": {"stage": "ACCEPTED"},
                "decision": {"proposed_category": "А", "classification_basis": "ML2_PROPOSAL"},
            }
        ],
        "usage": {"calls": 2},
        "retrieval_info": info,
        "evidence_snapshot": {"documents": [doc]},
    }
    return result, {
        "revision": "r1",
        "dimension": 2,
        "model": "test-only",
        "vectors": {key: [1.0, 0.0]},
    }


def test_group_citation_does_not_require_literal_inn_in_every_fragment():
    result, index = sample()
    assessed = assess(result, index, set(), require_categories=True)
    assert assessed["pair_count"] == assessed["fresh_relevant_vectors"] == 1
    assert assessed["category_counts"] == {"А": 1}


@pytest.mark.parametrize(
    "damage",
    ["fallback", "no_category", "no_vectors", "no_calls", "failure", "pending", "diagnostics"],
)
def test_model_success_and_no_content_leak_are_required(damage, capsys):
    result, index = sample()
    result = deepcopy(result)
    pair = result["pairs"][0]
    if damage == "fallback":
        pair["generation_mode"] = "FALLBACK"
    elif damage == "no_category":
        pair["decision"]["proposed_category"] = None
    elif damage == "no_vectors":
        index["vectors"] = {}
    elif damage == "no_calls":
        result["usage"]["calls"] = 0
    elif damage == "failure":
        pair["reasons"] = ["AI_UNAVAILABLE"]
    elif damage == "pending":
        result["unprocessed_pairs"] = [{"reason": "BUDGET_EXHAUSTED"}]
    else:
        pair["classification_diagnostics"] = None
    with pytest.raises(ValueError):
        assess(result, index, set(), require_categories=True)
    assert capsys.readouterr().out == ""
