import pytest
from pydantic import ValidationError

from tests.helpers import request_data


def parse(data):
    from llm_rag.contracts import AnalysisRequest
    return AnalysisRequest.model_validate(data)


def test_valid_request_preserves_upstream_and_decimal_amount():
    data = request_data()
    request = parse(data)
    assert request.items[0].original.amount.as_tuple().exponent == -2
    assert request.model_dump(mode="json")["items"] == data["items"]


@pytest.mark.parametrize("category", ["А", "Б", "В", None])
def test_accepts_only_proposed_upstream_categories(category):
    data = request_data()
    data["items"][0]["category"] = category
    assert parse(data).items[0].category == category


@pytest.mark.parametrize("path,value", [
    (("schema_version",), "2.0"), (("request_id",), "  "),
    (("is_demo",), False), (("is_demo",), "true"),
    (("items",), []), (("diagnosis",), ""),
    (("items", 0, "category"), "A"),
    (("items", 0, "original", "amount"), "-0.01"),
    (("items", 0, "original", "amount"), "NaN"),
    (("items", 0, "original", "amount"), True),
    (("items", 0, "original", "amount"), 1.3),
    (("items", 0, "original", "quantity"), True),
    (("items", 0, "original", "quantity"), 0),
    (("items", 0, "original", "currency"), "rub"),
    (("items", 0, "inn"), " "),
    (("items", 0, "dosage_check", "status"), "MADE_UP"),
])
def test_rejects_invalid_contract_fields(path, value):
    data = request_data()
    target = data
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    with pytest.raises(ValidationError):
        parse(data)


def test_rejects_duplicate_item_ids_and_unknown_fields():
    data = request_data()
    data["items"].append(data["items"][0].copy())
    with pytest.raises(ValidationError):
        parse(data)
    data = request_data()
    data["items"][0]["invented_diagnosis"] = "x"
    with pytest.raises(ValidationError):
        parse(data)
