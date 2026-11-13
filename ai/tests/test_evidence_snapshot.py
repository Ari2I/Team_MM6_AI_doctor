import pytest

from llm_rag.explanation_contracts import ExplanationInput
from llm_rag.evidence_snapshot import EvidenceSnapshot, verify_ml2_evidence


def sample():
    citation = dict(source_id='s', fragment_id='f', title='Синтетический источник',
                    version='2026', url='https://example.org/original',
                    copy_url='https://example.org/copy', location='§1', quote='Разрешённый текст.')
    value = ExplanationInput(request_id='r', item_id='i', diagnosis_id='d', diagnosis='Диагноз',
        inn='МНН', category='А', ml2_status='PROPOSED', rule_applied=True,
        classification_reason='Правило', citations=[citation],
        dosage_check=dict(status='INSUFFICIENT_DATA', reason=None, missing_data=['dosage']))
    doc = dict(citation, text='Вступление. Разрешённый текст.', diagnosis='Диагноз', inn='МНН',
               retrieved_at='2026-11-11T00:00:00Z', required_context={})
    doc.pop('quote')
    snap = EvidenceSnapshot(snapshot_version='s1', documents=[doc])
    return value, snap


def test_exact_quote_is_review_material_not_approved_category():
    value, snap = sample()
    evidence = verify_ml2_evidence(value, snap)
    assert evidence.citations[0].copy_url == 'https://example.org/copy'
    assert evidence.status == 'VERIFIED'
    assert evidence.approved_claims == []


@pytest.mark.parametrize('field,value', [('quote', 'Выдумано'), ('fragment_id', 'other'),
    ('version', '2025'), ('source_id', 'foreign'), ('url', 'https://evil.example')])
def test_foreign_or_forged_quote_is_rejected(field, value):
    inp, snap = sample()
    raw = inp.model_dump()
    raw['citations'][0][field] = value
    evidence = verify_ml2_evidence(ExplanationInput.model_validate(raw), snap)
    assert not evidence.citations
    assert evidence.status != 'VERIFIED'


def test_wrong_diagnosis_and_unknown_condition():
    inp, snap = sample()
    raw = snap.model_dump()
    raw['documents'][0]['diagnosis'] = 'Другой диагноз'
    assert not verify_ml2_evidence(inp, EvidenceSnapshot.model_validate(raw)).citations
    raw = snap.model_dump()
    raw['documents'][0]['required_context'] = {'age_group': 'adult'}
    evidence = verify_ml2_evidence(inp, EvidenceSnapshot.model_validate(raw))
    assert 'context.age_group' in evidence.missing_context
    assert evidence.status == 'INSUFFICIENT_DATA'


def test_empty_snapshot_is_insufficient():
    inp, _ = sample()
    assert verify_ml2_evidence(inp, EvidenceSnapshot(snapshot_version='empty')).status == 'INSUFFICIENT_DATA'


def test_exact_known_diagnosis_is_not_missing_context():
    inp, snap = sample()
    inp.conditions = [inp.diagnosis]
    assert verify_ml2_evidence(inp, snap).missing_context == []


def test_mismatched_condition_never_approves():
    inp, snap = sample()
    inp.context = {'age_group': 'child'}
    raw = snap.model_dump()
    raw['documents'][0]['required_context'] = {'age_group': 'adult'}
    ev = verify_ml2_evidence(inp, EvidenceSnapshot.model_validate(raw))
    assert not ev.citations
    assert ev.status == 'UNCONFIRMED'


def test_dosage_only_rule_does_not_require_weight_for_category():
    from llm_rag.corpus import Corpus
    inp, snap = sample()
    category = dict(claim_id='cat', kind='CATEGORY', category='А', dosage_status=None,
                    dosage=None, upstream_reason='Правило', text='Разрешённый текст.', quote='Разрешённый текст.')
    fragment = dict(fragment_id='f', source_id='s', location='§1', inn='МНН', diagnosis='Диагноз',
                    required_context={}, text='Разрешённый текст.', assertions=[category])
    dosage = dict(fragment, fragment_id='dose', required_context={'weight': '70'}, assertions=[
        dict(category, claim_id='dose', kind='DOSAGE', category=None, dosage_status='VALID', dosage='1')])
    corpus = Corpus.model_validate(dict(corpus_version='c1', is_demo=True,
        sources=[dict(source_id='s', title='Источник', source_type='PROTOCOL', version='2026', is_demo=True)],
        fragments=[fragment, dosage]))
    result = verify_ml2_evidence(inp, snap, approved_corpus=corpus)
    assert result.status == 'VERIFIED'
    assert result.approved_claims
    assert not result.missing_context


def test_incompatible_alternative_rule_does_not_require_its_missing_context():
    from llm_rag.corpus import Corpus
    from llm_rag.explanation import explain_pair

    inp, snap = sample()
    inp.context = {'age_group': 'adult'}
    assertion = dict(claim_id='adult', kind='CATEGORY', category='А', dosage_status=None,
        dosage=None, upstream_reason='Правило', text='Разрешённый текст.', quote='Разрешённый текст.')
    adult = dict(fragment_id='f', source_id='s', location='§1', inn='МНН', diagnosis='Диагноз',
        required_context={'age_group': 'adult'}, text='Разрешённый текст.', assertions=[assertion])
    child = dict(adult, fragment_id='child', required_context={'age_group': 'child', 'weight': '20'},
        assertions=[dict(assertion, claim_id='child')])
    corpus = Corpus.model_validate(dict(corpus_version='c1', is_demo=True,
        sources=[dict(source_id='s', title='Источник', source_type='PROTOCOL', version='2026', is_demo=True)],
        fragments=[adult, child]))

    evidence = verify_ml2_evidence(inp, snap, approved_corpus=corpus)
    result = explain_pair(inp, evidence)

    assert result.evidence_status == 'VERIFIED'
    assert result.classification_basis == 'APPROVED_RULE'
    assert result.missing_context == []
    assert [claim.claim_id for claim in evidence.approved_claims] == ['adult']
