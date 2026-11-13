"""Проверка опубликованных независимых Excel по заранее заданным ожиданиям."""

from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path

import pytest

from llm_rag.excel_scenario import read_card

CASES = Path(__file__).resolve().parents[1] / 'excel_cases' / 'review_20261112'
MANIFEST = json.loads((CASES / 'manifest.json').read_text(encoding='utf-8'))
HASHES = json.loads((CASES / 'SHA256.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('case', MANIFEST['cases'], ids=lambda case: case['id'])
def test_independent_excel_matches_predefined_input_expectations(case):
    path, = (CASES / 'input').glob(case['id'] + '_*.xlsx')
    assert sha256(path.read_bytes()).hexdigest() == HASHES[path.name]
    if case.get('invalid'):
        with pytest.raises(ValueError):
            read_card(path)
        return
    card = read_card(path)
    assert len(card.request.items) == len(case['rows'])
    assert ('age' in card.request.context) == (case['age'] is not None)
    totals = {}
    for item in card.request.items:
        currency = item.original.currency
        totals[currency] = totals.get(currency, Decimal('0')) + item.original.amount
    assert totals == {currency: Decimal(amount) for currency, amount in case['totals'].items()}
