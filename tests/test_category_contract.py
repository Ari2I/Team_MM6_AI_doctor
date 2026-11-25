"""Category codes stay Cyrillic across rules, API, aggregation and AI boundaries."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models.enums import DrugCategory
from app.schemas.analysis import PairCheck
from app.schemas.application_api import ApplicationCard
from app.scripts.import_reference import InputRule
from app.services.ai_adapter import backend_category, module_category
from app.services.summary import build_summary, category_shares

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("code", ["А", "Б", "В"])
def test_category_roundtrip_through_rule_api_and_ai(code: str) -> None:
    rule = InputRule(mnn="Демо-МНН", diagnosis_id="demo", category=code)
    check = PairCheck(category=rule.category)
    assert json.loads(check.model_dump_json())["category"] == code
    assert backend_category(module_category(check.category)) == DrugCategory(code)


@pytest.mark.parametrize("code", ["A", "B", "V", "C"])
def test_latin_category_codes_are_not_accepted_as_russian(code: str) -> None:
    with pytest.raises(ValidationError):
        PairCheck(category=code)


def test_amounts_and_summary_use_all_three_cyrillic_codes() -> None:
    shares = category_shares(
        [
            {"category": "А", "amount": "900.00"},
            {"category": "Б", "amount": "50.00"},
            {"category": "В", "amount": "50.00"},
        ]
    )
    assert shares == {
        "А": 90.0,
        "Б": 5.0,
        "В": 5.0,
        "undetermined": 0.0,
        "А_amount": 900.0,
        "Б_amount": 50.0,
        "В_amount": 50.0,
        "undetermined_amount": 0.0,
        "total_amount": 1000.0,
    }
    summary = build_summary(shares, "green")
    assert "категория А — 90.00%, Б — 5.00%, В — 5.00%" in summary
    assert "рекомендательный" in summary


def test_public_sample_uses_same_enum_as_openapi() -> None:
    card = ApplicationCard.model_validate_json(
        (ROOT / "sample_api_response.json").read_text("utf-8")
    )
    codes = {check.category.value for item in card.items for check in item.checks if check.category}
    assert codes == {"А", "Б", "В"}
    assert ApplicationCard.model_json_schema()["$defs"]["DrugCategory"]["enum"] == ["А", "Б", "В"]
