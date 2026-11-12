import pytest

from llm_rag.explanation import explain_pair
from llm_rag.evidence_snapshot import verify_ml2_evidence
from tests.test_evidence_snapshot import sample


def test_clinical_category_is_displayed_without_requiring_corpus():
    inp, snap = sample()
    result = explain_pair(inp, verify_ml2_evidence(inp, snap))
    assert result.category == 'А'
    assert result.classification_basis == 'ML2_PROPOSAL'
    assert result.evidence_status == 'VERIFIED'
    assert 'ML-2 присвоил категорию А' in result.comment
    assert 'по текстовому снимку' in result.comment
    assert 'предлагает' not in result.comment
    assert 'категория требует сверки' not in result.comment
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


@pytest.mark.parametrize('marker,label', [
    ('clinical_applicability', 'применимость клинических условий независимо не проверена'),
    ('citation_age_applicability', 'возрастные условия цитаты требуют отдельной сверки'),
])
def test_missing_applicability_markers_have_russian_comments_but_keep_machine_keys(marker, label):
    inp, snap = sample()
    inp.missing_context = [marker]
    result = explain_pair(inp, verify_ml2_evidence(inp, snap))
    assert result.missing_context == [marker]
    assert label in result.comment
    assert marker not in result.comment
    assert result.evidence_status == 'INSUFFICIENT_DATA'
