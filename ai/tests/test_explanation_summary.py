from decimal import Decimal, localcontext

import pytest

from llm_rag.explanation import explain_pair
from llm_rag.evidence_snapshot import verify_ml2_evidence
from llm_rag.explanation_contracts import PurchaseAmount
from llm_rag.explanation_summary import summarize_explanations
from tests.test_evidence_snapshot import sample


def pair():
    inp, snap = sample()
    return explain_pair(inp, verify_ml2_evidence(inp, snap))


def test_purchase_once_across_diagnoses_and_unknown_separately():
    first = pair()
    second = first.model_copy(update={'diagnosis_id': 'd2'})
    third = first.model_copy(update={'item_id': 'i2'})
    result = summarize_explanations([first, second, third], purchases=[
        PurchaseAmount(request_id='r', item_id='i', amount='12.50', currency='RUB'),
        PurchaseAmount(request_id='r', item_id='i2', amount=None, currency='RUB')])
    costs = result.costs_by_currency[0]
    assert costs.known_total_amount == Decimal('12.50')
    assert costs.unknown_amount_count == 1
    assert costs.amounts_by_upstream_category['А'] == Decimal('12.50')
    assert len(result.summary_refs) == 3
    assert result.unresolved_pair_ids == []
    assert result.requires_review
    assert 'компенсации не' in result.summary_text


def test_distinct_ids_and_currencies():
    p = pair()
    pairs = [p, p.model_copy(update={'item_id': 'i2'}), p.model_copy(update={'item_id': 'i3'})]
    result = summarize_explanations(pairs, purchases=[PurchaseAmount(request_id='r', item_id=i,
        amount='10', currency=c) for i, c in [('i', 'RUB'), ('i2', 'RUB'), ('i3', 'USD')]])
    assert {c.currency: c.known_total_amount for c in result.costs_by_currency} == {'RUB': Decimal('20'), 'USD': Decimal('10')}


def test_duplicate_purchases_rejected_and_missing_amount_not_zero():
    p = pair()
    amount = PurchaseAmount(request_id='r', item_id='i', amount='1', currency='RUB')
    with pytest.raises(ValueError):
        summarize_explanations([p], purchases=[amount, amount])
    result = summarize_explanations([p], purchases=[])
    assert result.purchases_without_amount == [['r', 'i']]


@pytest.mark.parametrize("changes", [
    {"evidence_status": "UNCONFIRMED"},
    {"evidence_status": "SOURCE_CONFLICT"},
    {"evidence_status": "INSUFFICIENT_DATA"},
    {"evidence_status": "PROCESSING_ERROR"},
    {"category": None, "classification_basis": "NONE"},
    {"ml2_status": "NEEDS_REVIEW"},
    {"missing_context": ["context.age"]},
])
def test_only_verified_completed_classifications_leave_unresolved(changes):
    unresolved = pair().model_copy(update=changes)
    result = summarize_explanations([unresolved], purchases=[])
    assert len(result.unresolved_pair_ids) == 1


def test_large_amounts_keep_exact_totals_in_every_breakdown_and_preserve_decimal_context():
    first = pair()
    large = '123456789012345678901234567890.01'
    pairs = [first, first.model_copy(update={'diagnosis_id': 'd2'}),
             first.model_copy(update={'item_id': 'i2', 'category': 'Б'}),
             first.model_copy(update={'item_id': 'i3'})]
    purchases = [PurchaseAmount(request_id='r', item_id='i', amount=large, currency='RUB'),
                 PurchaseAmount(request_id='r', item_id='i2', amount='0.09', currency='RUB'),
                 PurchaseAmount(request_id='r', item_id='i3', amount=None, currency='USD')]

    with localcontext() as context:
        context.prec = 6
        result = summarize_explanations(pairs, purchases=purchases)
        assert context.prec == 6

    costs = {c.currency: c for c in result.costs_by_currency}
    expected = Decimal('123456789012345678901234567890.10')
    assert costs['RUB'].known_total_amount == expected
    assert costs['RUB'].amount_requiring_review == expected
    assert costs['RUB'].amounts_by_upstream_category == {'А': Decimal(large), 'Б': Decimal('0.09')}
    assert costs['RUB'].amounts_by_diagnosis == {'d': expected, 'd2': Decimal(large)}
    assert costs['USD'].known_total_amount == Decimal('0')
    assert costs['USD'].unknown_amount_count == 1
    assert result.purchases_without_amount == [['r', 'i3']]
