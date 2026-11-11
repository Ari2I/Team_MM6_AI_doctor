import pytest

from llm_rag.explanation import explain_pair
from llm_rag.evidence_snapshot import verify_ml2_evidence
from tests.test_evidence_snapshot import sample


def test_proposal_is_not_approved_and_dosage_does_not_block():
    inp, snap = sample()
    result = explain_pair(inp, verify_ml2_evidence(inp, snap))
    assert result.category == 'А'
    assert result.classification_basis == 'ML2_PROPOSAL'
    assert result.evidence_status == 'VERIFIED'
    assert 'категория требует сверки' in result.comment
    assert 'Дозировка не оценивалась' in result.comment
    assert 'dosage' not in result.missing_context
    assert result.dosage_check == inp.dosage_check


@pytest.mark.parametrize('status,expected', [('CONFLICT', 'SOURCE_CONFLICT'),
    ('NEEDS_CONTEXT', 'INSUFFICIENT_DATA'), ('INN_UNRESOLVED', 'INSUFFICIENT_DATA'),
    ('MULTI_INGREDIENT_REVIEW', 'UNCONFIRMED'), ('NEEDS_REVIEW', 'UNCONFIRMED'),
    ('NO_EVIDENCE', 'UNCONFIRMED'), ('INVALID_RESPONSE', 'PROCESSING_ERROR'),
    ('SOURCE_ERROR', 'PROCESSING_ERROR'), ('PROVIDER_ERROR', 'PROCESSING_ERROR')])
def test_upstream_status_survives_good_quote(status, expected):
    inp, snap = sample()
    inp.ml2_status = status
    result = explain_pair(inp, verify_ml2_evidence(inp, snap))
    assert result.evidence_status == expected
    assert result.requires_review


def test_wrong_pair_evidence_rejected():
    inp, snap = sample()
    evidence = verify_ml2_evidence(inp, snap)
    evidence.item_id = 'other'
    with pytest.raises(ValueError):
        explain_pair(inp, evidence)
