from llm_rag.contracts import AnalysisRequest
from llm_rag.corpus import Corpus
from llm_rag.evidence_snapshot import EvidenceSnapshot, verify_ml2_evidence
from llm_rag.explanation import explain_pairs, render_explanation
from llm_rag.explanation_contracts import prepare_explanation_inputs, PurchaseAmount
from llm_rag.explanation_summary import ExplainedApplication, summarize_explanations
from llm_rag.ml2.pipeline import ML2Pipeline
from llm_rag.ml2.registry import RegistrySnapshot
from tests.test_explanation_generation import Provider
from tests.helpers import ROOT


def fixture():
    request = AnalysisRequest.model_validate_json((ROOT / 'examples/request.json').read_bytes())
    registry = RegistrySnapshot.model_validate_json((ROOT / 'examples/ml2/registry.synthetic.json').read_bytes())
    corpus = Corpus.model_validate_json((ROOT / 'examples/corpus.json').read_bytes())
    return request, registry, corpus


def snapshot_for(corpus, request):
    return EvidenceSnapshot(snapshot_version='synthetic-1', documents=[dict(
        source_id=f.source_id, fragment_id=f.fragment_id, title='Синтетический источник',
        version=next(s.version for s in corpus.sources if s.source_id == f.source_id),
        url='https://example.org/demo', location=f.location, text=f.text,
        diagnosis=f.diagnosis, inn=f.inn, required_context=f.required_context,
        retrieved_at='2026-11-11T00:00:00Z') for f in corpus.fragments])


def test_real_ml2_batch_to_json_and_old_pipeline():
    request, registry, corpus = fixture()
    batch = ML2Pipeline(registry, approved_corpus=corpus).run(request)
    inputs = prepare_explanation_inputs(batch.request, diagnosis_id='d1', ml2_details=batch.details())
    snapshot = snapshot_for(corpus, request)
    results = explain_pairs(inputs, snapshot, approved_corpus=corpus)
    assert any(p.classification_basis == 'APPROVED_RULE' for p in results)
    app = summarize_explanations(results, purchases=[PurchaseAmount(request_id=batch.request.request_id,
        item_id=i.item_id, amount=i.original.amount, currency=i.original.currency) for i in batch.request.items])
    assert ExplainedApplication.model_validate_json(app.model_dump_json()) == app
    from llm_rag.pipeline import Pipeline
    from llm_rag.providers.demo import DemoProvider
    old = AnalysisRequest.model_validate_json((ROOT / 'examples/request.json').read_bytes())
    assert Pipeline(corpus, DemoProvider()).run(old).request_id == old.request_id


def test_pair_failure_isolated(monkeypatch):
    from tests.test_evidence_snapshot import sample
    value, snapshot = sample()
    other = value.model_copy(update={'item_id': 'other'})
    original = __import__('llm_rag.explanation', fromlist=['render_explanation']).render_explanation
    def fail_one(inp, *args, **kwargs):
        if inp.item_id == value.item_id:
            raise RuntimeError('закрытые данные')
        return original(inp, *args, **kwargs)
    monkeypatch.setattr('llm_rag.explanation.render_explanation', fail_one)
    pairs = explain_pairs([value, other], snapshot)
    assert pairs[0].evidence_status == 'PROCESSING_ERROR'
    assert pairs[1].evidence_status == 'VERIFIED'
    assert 'закрытые' not in pairs[0].model_dump_json()


def test_proposal_context_unknown_inn_conflict_and_model_failure():
    from tests.test_evidence_snapshot import sample
    inp, snap = sample()
    variants = [inp.model_copy(update={'item_id': 'proposal', 'rule_applied': False}),
                inp.model_copy(update={'item_id': 'age', 'ml2_status': 'NEEDS_CONTEXT', 'missing_context': ['context.age']}),
                inp.model_copy(update={'item_id': 'inn', 'inn': None, 'ml2_status': 'INN_UNRESOLVED'}),
                inp.model_copy(update={'item_id': 'conflict', 'ml2_status': 'CONFLICT'})]
    results = explain_pairs(variants, snap, provider=Provider('timeout'))
    assert [p.evidence_status for p in results] == ['VERIFIED', 'INSUFFICIENT_DATA', 'INSUFFICIENT_DATA', 'SOURCE_CONFLICT']
    assert results[0].rendering_reasons
    assert 'context.age' in results[1].missing_context
    result = summarize_explanations(results, purchases=[])
    assert ExplainedApplication.model_validate_json(result.model_dump_json()) == result


def test_corpus_conflicts_and_versions_cannot_be_hidden():
    req, registry, corpus = fixture()
    batch = ML2Pipeline(registry, approved_corpus=corpus).run(req)
    inp = prepare_explanation_inputs(batch.request, diagnosis_id='d', ml2_details=batch.details())[0]
    snap = snapshot_for(corpus, req)
    evidence = verify_ml2_evidence(inp, snap, approved_corpus=corpus)
    assert evidence.approved_claims
    inp.rule_applied = False
    assert verify_ml2_evidence(inp, snap, approved_corpus=corpus).approved_claims
    raw = corpus.model_dump()
    fragment = next(f for f in raw['fragments'] if f['fragment_id'] == inp.citations[0].fragment_id)
    import copy
    other = copy.deepcopy(fragment)
    other['fragment_id'] = 'conflicting-rule'
    other['assertions'] = [a for a in other['assertions'] if a['kind'] == 'CATEGORY']
    for a in other['assertions']:
        a.update(claim_id='other-category', category='Б')
    raw['fragments'].append(other)
    conflict = verify_ml2_evidence(inp, snap, approved_corpus=Corpus.model_validate(raw))
    assert conflict.status == 'SOURCE_CONFLICT'
    assert not conflict.approved_claims
    raw = corpus.model_dump()
    for s in raw['sources']:
        s['version'] = 'other-version'
    assert not verify_ml2_evidence(inp, snap, approved_corpus=Corpus.model_validate(raw)).approved_claims


def test_published_example_matches_expected_json():
    import json
    from llm_rag.explanation_contracts import ExplanationInput
    data = json.loads((ROOT / 'examples/ml2_explanation.json').read_text(encoding='utf-8'))
    pairs = explain_pairs([ExplanationInput.model_validate(i) for i in data['inputs']],
        EvidenceSnapshot.model_validate(data['snapshot']), approved_corpus=Corpus.model_validate(data['approved_corpus']))
    result = summarize_explanations(pairs, purchases=[PurchaseAmount.model_validate(p) for p in data['purchases']])
    assert result.model_dump(mode='json') == data['expected_result']
