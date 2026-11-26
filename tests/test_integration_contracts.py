"""Публичный вход библиотеки: идентичность и неполные данные без сети."""

from decimal import Decimal
import json
from pathlib import Path

from pydantic import ValidationError
import pytest


def request_data(count=2):
    return dict(
        schema_version="integration-request.1",
        request_id="R1",
        diagnoses=[dict(diagnosis_id=f"D{i}", name=f"Диагноз {i}") for i in range(count)],
        purchases=[dict(item_id="P1", name="Средство"), dict(item_id="P2", name="Средство")],
        links=[dict(item_id="P1", diagnosis_id=f"D{i}") for i in range(count)],
    )


def model():
    from llm_rag.integration_contracts import IntegrationRequest

    return IntegrationRequest


@pytest.mark.parametrize("count", [1, 2, 10, 50])
def test_pair_identity(count):
    request = model().model_validate(request_data(count))
    assert len(request.diagnoses) == count
    assert len(request.links) == count
    assert [p.item_id for p in request.purchases] == ["P1", "P2"]


def test_nullable_purchase_fields():
    request = model().model_validate(request_data())
    purchase = request.purchases[0]
    for field in ("inn", "dosage", "quantity", "amount", "currency"):
        assert getattr(purchase, field) is None
        assert json.loads(request.model_dump_json())["purchases"][0][field] is None


def test_fractional_quantity():
    data = request_data()
    data["purchases"][0].update(quantity="0.5", amount="0.00", currency="RUB")
    request = model().model_validate(data)
    assert request.purchases[0].quantity == Decimal("0.5")
    assert request.purchases[0].amount == Decimal("0")
    assert model().model_validate_json(request.model_dump_json()) == request


@pytest.mark.parametrize(
    "change",
    [
        "unknown_item",
        "unknown_diagnosis",
        "repeat_link",
        "repeat_item",
        "repeat_diagnosis",
        "no_diagnoses",
        "no_purchases",
        "no_links",
    ],
)
def test_invalid_links(change):
    data = request_data()
    if change == "unknown_item":
        data["links"][0]["item_id"] = "missing"
    elif change == "unknown_diagnosis":
        data["links"][0]["diagnosis_id"] = "missing"
    elif change == "repeat_link":
        data["links"].append(data["links"][0].copy())
    elif change == "repeat_item":
        data["purchases"].append(data["purchases"][0].copy())
    elif change == "repeat_diagnosis":
        data["diagnoses"].append(data["diagnoses"][0].copy())
    else:
        data[{"no_diagnoses": "diagnoses", "no_purchases": "purchases", "no_links": "links"}[change]] = []
    with pytest.raises(ValidationError):
        model().model_validate(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("quantity", "0"),
        ("quantity", "-1"),
        ("quantity", True),
        ("quantity", 0.5),
        ("amount", "-1"),
        ("amount", "NaN"),
        ("amount", "Infinity"),
        ("amount", True),
        ("amount", 0.1),
        ("currency", "rub"),
        ("currency", ""),
        ("name", "  "),
    ],
)
def test_invalid_purchase_values(field, value):
    data = request_data()
    data["purchases"][0][field] = value
    with pytest.raises(ValidationError):
        model().model_validate(data)


@pytest.mark.parametrize("field", ["is_demo", "demo_mode", "profile", "context"])
def test_no_demo_flags_in_new_runtime_contract(field):
    data = request_data()
    data[field] = True
    with pytest.raises(ValidationError):
        model().model_validate(data)


def test_local_conditions_and_pinned_versions():
    data = request_data()
    data["diagnoses"][0]["conditions"] = {"age_years": "38.5", "pregnant": None}
    data["versions"] = {"registry": "registry-1", "clinical": "clinical-1", "corpus": None, "policy": None}
    request = model().model_validate(data)
    assert request.diagnoses[0].conditions.age_years == Decimal("38.5")
    assert request.versions.policy is None
    data["diagnoses"][0]["conditions"]["free_text"] = "Произвольные данные"
    with pytest.raises(ValidationError):
        model().model_validate(data)


@pytest.mark.parametrize(
    "conditions,group,years",
    [
        ({}, None, None),
        ({"age_group": None}, None, None),
        ({"age_group": "adult"}, "adult", None),
        ({"age_group": "child"}, "child", None),
        ({"age_years": "17.99"}, None, Decimal("17.99")),
        ({"age_group": "child", "age_years": "17.99"}, "child", Decimal("17.99")),
        ({"age_group": "adult", "age_years": "18"}, "adult", Decimal("18")),
    ],
)
def test_public_age_group_is_optional_and_preserves_original_age(conditions, group, years):
    data = request_data()
    data["diagnoses"][0]["conditions"] = conditions
    request = model().model_validate_json(json.dumps(data))
    actual = request.diagnoses[0].conditions
    assert actual.age_group == group
    assert actual.age_years == years
    assert model().model_validate_json(request.model_dump_json()) == request


@pytest.mark.parametrize("group,years", [("adult", "17.99"), ("child", "18")])
def test_public_age_group_rejects_conflicting_exact_age(group, years):
    data = request_data()
    data["diagnoses"][0]["conditions"] = {"age_group": group, "age_years": years}
    with pytest.raises(ValidationError, match="противоречит"):
        model().model_validate(data)


@pytest.mark.parametrize("group", ["other", "Adult", "Взрослые", True])
def test_public_age_group_rejects_non_contract_values(group):
    data = request_data()
    data["diagnoses"][0]["conditions"] = {"age_group": group}
    with pytest.raises(ValidationError) as error:
        model().model_validate(data)
    assert error.value.errors()[0]["type"] == "literal_error"


def test_independent_fixture_matrix_matches_public_contract():
    path = Path(__file__).resolve().parents[1] / "excel_cases/multidiagnosis_20261117/manifest.json"
    cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
    for case in cases:
        data = dict(
            schema_version="integration-request.1",
            request_id=case["id"],
            diagnoses=[
                dict(
                    diagnosis_id=d[0],
                    name=d[1],
                    icd10=d[2],
                    conditions={"age_years": str(d[3]) if d[3] is not None else None},
                )
                for d in case["diagnoses"]
            ],
            purchases=[
                dict(
                    item_id=p[0], name=p[1], dosage=p[2], quantity=str(p[3]), amount=str(p[4]), currency=p[5]
                )
                for p in case["purchases"]
            ],
            links=[dict(item_id=item, diagnosis_id=diagnosis) for item, diagnosis in case["links"]],
        )
        if case["expected_input_error"]:
            with pytest.raises(ValidationError):
                model().model_validate(data)
        else:
            request = model().model_validate(data)
            assert len(request.links) == len(case["links"])
            assert len(request.diagnoses) == case["diagnosis_count"]


@pytest.mark.parametrize("change", ["version", "blank_id", "extra_purchase", "extra_conditions"])
def test_strict_schema_boundaries(change):
    data = request_data()
    if change == "version":
        data["schema_version"] = "integration-request.2"
    elif change == "blank_id":
        data["diagnoses"][0]["diagnosis_id"] = " "
    elif change == "extra_purchase":
        data["purchases"][0]["is_demo"] = True
    else:
        data["diagnoses"][0]["conditions"] = {"context": {"private": "text"}}
    with pytest.raises(ValidationError):
        model().model_validate(data)
