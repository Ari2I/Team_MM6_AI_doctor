import pytest

from llm_rag.explanation import render_explanation
from llm_rag.evidence_snapshot import verify_ml2_evidence
from tests.test_evidence_snapshot import sample


class Provider:
    name = 'test'

    def __init__(self, change=None):
        self.change = change
        self.payload = None

    def generate(self, payload):
        self.payload = payload
        if self.change == 'timeout':
            raise TimeoutError('секретная техническая строка')
        response = dict(item_id=payload['item']['item_id'], claims=payload['evidence'])
        if self.change == 'pair':
            response['item_id'] = 'other'
        elif self.change:
            response['claims'][0][self.change] = 'Выдуманное утверждение'
        return response


@pytest.mark.parametrize('change', ['pair', 'text', 'quote', 'claim_id', 'timeout'])
def test_model_cannot_change_claim_or_pair(change):
    inp, snap = sample()
    evidence = verify_ml2_evidence(inp, snap)
    baseline = render_explanation(inp, evidence)
    result = render_explanation(inp, evidence, provider=Provider(change))
    assert result.comment == baseline.comment
    assert result.evidence_status == baseline.evidence_status
    assert result.rendering_reasons
    assert 'секретная' not in result.model_dump_json()


def test_payload_allowlist_and_valid_response():
    inp, snap = sample()
    inp.context = {'secret': 'PRIVATE'}
    inp.classification_reason = 'PRIVATE'
    provider = Provider()
    result = render_explanation(inp, verify_ml2_evidence(inp, snap), provider=provider)
    assert 'PRIVATE' not in str(provider.payload)
    assert not result.rendering_reasons


def test_no_evidence_does_not_call_model():
    inp, snap = sample()
    inp.ml2_status = 'NO_EVIDENCE'
    provider = Provider()
    render_explanation(inp, verify_ml2_evidence(inp, snap), provider=provider)
    assert provider.payload is None


def test_model_orders_only_permitted_extracts():
    from llm_rag.evidence_snapshot import EvidenceSnapshot
    inp, snap = sample()
    second = inp.citations[0].model_copy(update={'fragment_id': 'f2', 'quote': 'Вторая цитата.'})
    inp.citations.append(second)
    doc = snap.documents[0].model_copy(update={'fragment_id': 'f2', 'text': 'Вторая цитата.'})
    snap.documents.append(doc)
    evidence = verify_ml2_evidence(inp, snap)
    class Reorder(Provider):
        def generate(self, payload):
            response = super().generate(payload)
            response['claims'].reverse()
            return response
    result = render_explanation(inp, evidence, provider=Reorder())
    assert 'Вторая цитата.' not in result.comment
    assert 'Разрешённый текст.' not in result.comment
    assert result.citations[0].fragment_id == 'f2'
