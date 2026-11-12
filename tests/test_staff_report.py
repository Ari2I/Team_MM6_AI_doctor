import pytest

from llm_rag.staff_report import recommend_for_staff
from llm_rag.explanation import explain_pair
from llm_rag.evidence_snapshot import verify_ml2_evidence
from tests.test_evidence_snapshot import sample
from tests.test_gigachat import sdk_wire, make_provider, completion


class ReportProvider:
    name = 'test'

    def __init__(self, change=None):
        self.change = change
        self.payload = None

    def generate(self, payload):
        self.payload = payload
        if self.change == 'timeout':
            raise TimeoutError('SECRET')
        claims = [{k: c[k] for k in ('claim_id', 'text', 'fragment_id', 'quote')} for c in payload['evidence']]
        result = dict(item_id=payload['item']['item_id'], claims=claims)
        if self.change == 'pair':
            result['item_id'] = 'wrong'
        elif self.change == 'text':
            result['claims'][0]['text'] = 'Назначить лечение и выплатить 100%.'
        elif self.change == 'missing':
            result['claims'].pop()
        elif self.change == 'variant':
            result['claims'][0]['text'] = payload['evidence'][0]['allowed_texts'][0]
        return result


def values(status='NEEDS_REVIEW'):
    inp, snap = sample()
    inp.ml2_status = status
    pair = explain_pair(inp, verify_ml2_evidence(inp, snap))
    return inp, pair


def test_report_calls_model_for_needs_review_and_no_evidence():
    for status in ['NEEDS_REVIEW', 'NO_EVIDENCE']:
        inp, pair = values(status)
        provider = ReportProvider()
        result = recommend_for_staff([inp], [pair], provider=provider)
        assert provider.payload is not None
        assert result.generation_status == 'MODEL'
        assert result.recommendations[0].actions
        text = ' '.join(result.recommendations[0].actions)
        assert 'возраст' in text.lower()
        assert 'специалист' in text.lower()
        if status == 'NO_EVIDENCE':
            assert 'сопутствующий диагноз' in text


@pytest.mark.parametrize('change', ['timeout', 'pair', 'text', 'missing'])
def test_bad_model_report_keeps_all_required_actions(change):
    inp, pair = values()
    expected = recommend_for_staff([inp], [pair])
    report = recommend_for_staff([inp], [pair], provider=ReportProvider(change))
    assert report.generation_status == 'FALLBACK'
    assert report.recommendations == expected.recommendations
    assert 'SECRET' not in report.model_dump_json()


def test_report_does_not_send_context_or_money_and_accepts_allowed_variant():
    inp, pair = values()
    inp.context = {'secret': 'PRIVATE'}
    inp.classification_reason = 'PRIVATE'
    provider = ReportProvider('variant')
    report = recommend_for_staff([inp], [pair], provider=provider)
    assert report.generation_status == 'MODEL'
    assert 'PRIVATE' not in str(provider.payload)
    assert 'amount' not in str(provider.payload)


def test_report_rejects_foreign_pair():
    inp, pair = values()
    pair.item_id = 'foreign'
    with pytest.raises(ValueError):
        recommend_for_staff([inp], [pair])


def test_large_report_is_generated_in_bounded_batches():
    import json
    inp, pair = values()
    inputs = [inp.model_copy(update={'item_id': str(i)}) for i in range(5)]
    pairs = [pair.model_copy(update={'item_id': str(i)}) for i in range(5)]
    class Bounded(ReportProvider):
        def __init__(self):
            super().__init__()
            self.batches = []
        def generate(self, payload):
            self.batches.append(json.loads(payload['item']['item_id']))
            return super().generate(payload)
    provider = Bounded()
    report = recommend_for_staff(inputs, pairs, provider=provider)
    assert report.generation_status == 'MODEL'
    assert len(provider.batches) == 3
    assert all(len(batch) <= 2 for batch in provider.batches)
    assert len(report.recommendations) == 5


def test_sdk_report_uses_staff_instructions_and_variants(sdk_wire):
    import httpx
    import json
    def handler(request):
        if request.url.path == '/api/v2/oauth':
            return httpx.Response(200, json={'access_token': 'test-access', 'expires_at': 1_900_000_000_000})
        body = json.loads(request.content)
        assert 'административные действия' in body['messages'][0]['content']
        context = json.loads(body['messages'][1]['content'])
        assert context['schema_version'] == 'staff-report.1'
        claims = [{k: c[k] for k in ('claim_id', 'text', 'fragment_id', 'quote')} for c in context['evidence']]
        claims[0]['text'] = context['evidence'][0]['allowed_texts'][0]
        return httpx.Response(200, json=completion(dict(item_id=context['item']['item_id'], claims=claims)))
    sdk_wire.handler = handler
    inp, pair = values()
    with make_provider() as provider:
        report = recommend_for_staff([inp], [pair], provider=provider)
        assert report.generation_status == 'MODEL'
        assert provider.usage_totals['total_tokens'] == 140


@pytest.mark.parametrize("category", ["А", "Б", "В"])
@pytest.mark.parametrize("variant", [None, "variant"])
def test_completed_clinical_category_does_not_require_approved_corpus(category, variant):
    inp, pair = values("PROPOSED")
    inp.category = pair.category = category
    inp.rule_applied = False
    assert pair.evidence_status == "VERIFIED"
    assert pair.classification_basis == "ML2_PROPOSAL"
    report = recommend_for_staff([inp], [pair], provider=ReportProvider(variant))
    assert report.generation_status == "MODEL"
    text = " ".join(report.recommendations[0].actions)
    assert f"Категория {category}" in text
    assert "присвоена" in text
    assert "согласованное основание для" not in text
    assert "Не подтверждайте категорию" not in text
    assert "выплат" in text
    assert pair.category == category


@pytest.mark.parametrize("status", ["UNCONFIRMED", "INSUFFICIENT_DATA", "SOURCE_CONFLICT", "PROCESSING_ERROR"])
def test_unverified_evidence_never_gets_success_recommendation(status):
    inp, pair = values("PROPOSED")
    pair.evidence_status = status
    report = recommend_for_staff([inp], [pair])
    text = " ".join(report.recommendations[0].actions)
    assert "Категория А присвоена" not in text


@pytest.mark.parametrize('variant', [None, 'variant'])
def test_staff_actions_name_unverified_clinical_and_age_conditions_in_russian(variant):
    inp, snap = sample()
    inp.missing_context = ['clinical_applicability', 'citation_age_applicability']
    inp.context = {'age': '45', 'secret': 'PRIVATE'}
    pair = explain_pair(inp, verify_ml2_evidence(inp, snap))
    provider = ReportProvider(variant)
    report = recommend_for_staff([inp], [pair], provider=provider)
    text = ' '.join(report.recommendations[0].actions)
    assert 'применимость клинических условий независимо не проверена' in text
    assert 'возрастные условия цитаты требуют отдельной сверки' in text
    assert 'clinical_applicability' not in text
    assert 'citation_age_applicability' not in text
    assert 'PRIVATE' not in str(provider.payload)
    assert report.generation_status == 'MODEL'
