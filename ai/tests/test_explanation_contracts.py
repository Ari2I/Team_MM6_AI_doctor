import pytest

from llm_rag.contracts import AnalysisRequest
from llm_rag.explanation_contracts import ExplanationInput, prepare_explanation_inputs
from tests.helpers import ROOT


def request():
    return AnalysisRequest.model_validate_json((ROOT / 'examples/request.json').read_bytes())


def test_missing_details_and_round_trip():
    req = request()
    inputs = prepare_explanation_inputs(req, diagnosis_id='d1', ml2_details={})
    assert len(inputs) == len(req.items)
    assert 'ml2_details' in inputs[0].missing_context
    assert ExplanationInput.model_validate_json(inputs[0].model_dump_json()) == inputs[0]


def test_rejects_wrong_request_and_duplicate_or_unknown_items():
    req = request()
    with pytest.raises(ValueError):
        prepare_explanation_inputs(req, diagnosis_id='d', ml2_details={'request_id': 'other'})
    for ids in [['unknown'], [req.items[0].item_id] * 2]:
        with pytest.raises(ValueError):
            prepare_explanation_inputs(req, diagnosis_id='d', ml2_details={
                'items': [{'classification': {'item_id': i}} for i in ids]})


def test_shuffled_details_and_wrong_diagnosis():
    from llm_rag.ml2.pipeline import ML2Pipeline
    from tests.test_ml2_explanation_flow import fixture
    req, registry, corpus = fixture()
    batch = ML2Pipeline(registry, approved_corpus=corpus).run(req)
    details = batch.details()
    details['items'].reverse()
    inputs = prepare_explanation_inputs(batch.request, diagnosis_id='d', ml2_details=details)
    assert [i.item_id for i in inputs] == [i.item_id for i in batch.request.items]
    details['items'][0]['classification']['diagnosis'] = 'Чужой диагноз'
    with pytest.raises(ValueError):
        prepare_explanation_inputs(batch.request, diagnosis_id='d', ml2_details=details)


def test_missing_identity_metadata_is_not_silently_inferred():
    from llm_rag.ml2.pipeline import ML2Pipeline
    from tests.test_ml2_explanation_flow import fixture
    req, registry, corpus = fixture()
    batch = ML2Pipeline(registry, approved_corpus=corpus).run(req)
    details = batch.details()
    del details['request_id']
    del details['schema_version']
    for row in details['items']:
        del row['classification']['request_id']
        del row['classification']['diagnosis']
    inp = prepare_explanation_inputs(batch.request, diagnosis_id='d', ml2_details=details)[0]
    assert 'ml2_details.request_id' in inp.missing_context
    assert 'classification.diagnosis' in inp.missing_context
