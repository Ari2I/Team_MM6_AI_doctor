"""Допустимые денежные значения сохраняются без потери из-за диапазона Decimal."""

from decimal import Decimal, getcontext

import pytest

from llm_rag.contracts import AnalysisRequest
from llm_rag.explanation_contracts import PurchaseAmount
from llm_rag.explanation_summary import summarize_explanations
from llm_rag.summary import calculate_costs
from tests.helpers import request_data
from tests.test_explanation_summary import pair


@pytest.mark.parametrize('legacy', [False, True])
def test_valid_tiny_amount_remains_exact_and_does_not_change_decimal_context(legacy):
    amount = '1e-1000050'
    context = getcontext()
    original = (context.prec, context.Emin, context.Emax)
    if legacy:
        data = request_data()
        data['items'][0]['original']['amount'] = amount
        result = calculate_costs(AnalysisRequest.model_validate(data))[0]
        total = result.total_amount
        assert result.shares_by_upstream_category['А'] == Decimal('1.000000')
    else:
        result = summarize_explanations([pair()], purchases=[
            PurchaseAmount(request_id='r', item_id='i', amount=amount, currency='RUB')
        ]).costs_by_currency[0]
        total = result.known_total_amount
        assert result.amount_requiring_review == Decimal(amount)
        assert result.amounts_by_diagnosis['d'] == Decimal(amount)
    assert total == Decimal(amount)
    assert result.amounts_by_upstream_category['А'] == Decimal(amount)
    assert (context.prec, context.Emin, context.Emax) == original
