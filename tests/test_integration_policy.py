"""Раздельные категории и денежные итоги без медицинских догадок."""

from decimal import Decimal, localcontext
import pytest
from pydantic import ValidationError
from llm_rag.integration_contracts import IntegrationRequest


@pytest.mark.parametrize(
    "upstream,proposal,basis",
    [
        ("А", "Б", "CONFLICT"),
        ("А", "А", "APPROVED_RULE"),
        ("Б", None, "APPROVED_RULE"),
        (None, "В", "ML2_PROPOSAL"),
        (None, None, "NONE"),
    ],
)
def test_category_origins_remain_separate(upstream, proposal, basis):
    from llm_rag.integration_policy import category_decision

    result = category_decision(upstream, proposal)
    assert result.upstream_rule_category == upstream
    assert result.proposed_category == proposal
    assert result.classification_basis == basis
    assert result.requires_review is True


@pytest.mark.parametrize("category", ["A", "B", "V"])
def test_latin_categories_are_rejected(category):
    from llm_rag.integration_policy import category_decision

    with pytest.raises(ValidationError):
        category_decision(category, None)


def test_money_uses_unique_purchases_and_separate_currencies():
    from llm_rag.integration_policy import purchase_totals

    request = IntegrationRequest.model_validate(
        dict(
            schema_version="integration-request.1",
            request_id="R",
            diagnoses=[dict(diagnosis_id="D1", name="Один"), dict(diagnosis_id="D2", name="Два")],
            purchases=[
                dict(item_id="P", name="Средство", amount="100.10", currency="RUB"),
                dict(item_id="Q", name="Средство", amount="2.20", currency="USD"),
                dict(item_id="X", name="Неизвестно"),
                dict(item_id="Y", name="Нет валюты", amount="3"),
            ],
            links=[dict(item_id="P", diagnosis_id="D1"), dict(item_id="P", diagnosis_id="D2")],
        )
    )
    result = purchase_totals(request)
    assert result.totals == {"RUB": Decimal("100.10"), "USD": Decimal("2.20")}
    assert result.unknown_amount_item_ids == ["X"]
    assert result.unknown_currency_item_ids == ["X", "Y"]


def test_currency_totals_can_exceed_purchase_limit_and_remain_exact():
    from llm_rag.integration_policy import purchase_totals
    from tests.test_integration import request

    value = request(1)
    first = value.purchases[0]
    first.amount = Decimal("999999999999.999")
    value.purchases.extend([
        first.model_copy(update={"item_id": "P2", "amount": Decimal("0.002")}),
        first.model_copy(update={"item_id": "P3", "currency": "USD", "amount": Decimal("0.001")}),
    ])
    with localcontext() as context:
        context.prec = 4
        assert purchase_totals(value).totals == {"RUB": Decimal("1000000000000.001"), "USD": Decimal("0.001")}
        assert context.prec == 4
