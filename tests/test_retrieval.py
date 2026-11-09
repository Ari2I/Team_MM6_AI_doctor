from copy import deepcopy

import pytest
from pydantic import ValidationError

from llm_rag.contracts import AnalysisRequest
from tests.helpers import corpus_data, request_data


def load(data=None):
    from llm_rag.corpus import Corpus
    return Corpus.model_validate(corpus_data() if data is None else data)


def search(data, corpus=None):
    from llm_rag.retrieval import Retriever
    request = AnalysisRequest.model_validate(data)
    return Retriever(load(corpus)).search(request.items[0], request.diagnosis)


@pytest.mark.parametrize("inn,expected", [
    ("демо-вещество-а", ["master-a", "dose-a"]),
    ("демо-вещество-б", ["master-b"]), ("демо-вещество-в", ["master-c"]),
])
def test_exact_applicability_and_reference_ranking(inn, expected):
    data = request_data()
    data["items"][0]["inn"] = inn
    assert [f.fragment_id for f in search(data)] == expected


def test_reference_cannot_override_diagnosis_or_inn():
    data = request_data()
    data["diagnosis"] = "другой диагноз"
    assert search(data) == []
    data = request_data()
    data["items"][0]["inn"] = "неизвестное"
    data["items"][0]["source_refs"] = ["master-a"]
    assert search(data) == []


def test_normalizes_matching_without_changing_original_fields():
    data = request_data()
    data["diagnosis"] = "  ДЕМО-ДИАГНОЗ  "
    data["items"][0]["inn"] = " Демо-вещество-А "
    assert {f.fragment_id for f in search(data)} == {"master-a", "dose-a"}


def test_preserves_locations_and_retrieves_all_possible_conflicts():
    corpus = corpus_data()
    extra = deepcopy(corpus["fragments"][0])
    extra["fragment_id"] = "conflict-a"
    extra["assertions"][0]["claim_id"] = "conflict-a-claim"
    corpus["fragments"].append(extra)
    results = search(request_data(), corpus)
    assert len(results) == 3
    assert next(f for f in results if f.fragment_id == "master-a").location == "запись DEMO-A"


@pytest.mark.parametrize("mutation", ["duplicate_fragment", "duplicate_claim", "unknown_source", "missing_location", "fabricated_quote", "unsupported_text", "non_demo", "duplicate_source", "bad_assertion"])
def test_rejects_incoherent_corpus(mutation):
    corpus = corpus_data()
    if mutation == "duplicate_fragment":
        corpus["fragments"].append(deepcopy(corpus["fragments"][0]))
    elif mutation == "duplicate_claim":
        corpus["fragments"][1]["assertions"][0]["claim_id"] = "category-a"
    elif mutation == "unknown_source":
        corpus["fragments"][0]["source_id"] = "missing"
    elif mutation == "missing_location":
        corpus["fragments"][0]["location"] = " "
    elif mutation == "fabricated_quote":
        corpus["fragments"][0]["assertions"][0]["quote"] = "Вымышленная цитата"
    elif mutation == "unsupported_text":
        corpus["fragments"][0]["assertions"][0]["text"] = "Иной вывод при верной цитате"
    elif mutation == "non_demo":
        corpus["sources"][0]["is_demo"] = False
    elif mutation == "duplicate_source":
        corpus["sources"].append(deepcopy(corpus["sources"][0]))
    elif mutation == "bad_assertion":
        corpus["fragments"][0]["assertions"][0]["dosage_status"] = "VALID"
    with pytest.raises(ValidationError):
        load(corpus)
