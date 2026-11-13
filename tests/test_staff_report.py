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
        self.payloads = []

    def generate(self, payload):
        self.payload = payload
        self.payloads.append(payload)
        if self.change == 'timeout':
            raise TimeoutError('SECRET')
        result = substantive_response(payload)
        if self.change == 'pair':
            result['recommendations'][0]['pair_id'] = 'wrong'
        elif self.change == 'text':
            result['recommendations'][0]['established'][0]['text'] = 'Назначить лечение и выплатить 100%.'
        elif self.change == 'missing':
            result['recommendations'].pop()

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
            self.batches.append([p['pair_id'] for p in payload['pairs']])
            return super().generate(payload)
    provider = Bounded()
    report = recommend_for_staff(inputs, pairs, provider=provider)
    assert report.generation_status == 'MODEL'
    assert len(provider.batches) == 5
    assert all(len(batch) == 1 for batch in provider.batches)
    assert len(report.recommendations) == 5


def test_sdk_report_uses_staff_instructions_and_variants(sdk_wire):
    import httpx
    import json
    def handler(request):
        if request.url.path == '/api/v2/oauth':
            return httpx.Response(200, json={'access_token': 'test-access', 'expires_at': 1_900_000_000_000})
        body = json.loads(request.content)
        assert 'проверенных фактов' in body['messages'][0]['content']
        context = json.loads(body['messages'][1]['content'])
        assert context['schema_version'] == 'staff-facts.2'
        schema = body['response_format']['schema']
        assert schema['properties']['uncertain'] == {'type': 'string', 'minLength': 1}
        assert 'established' not in schema['properties']
        assert 'maxLength' not in str(schema)
        assert 'citations' not in schema['properties'] and 'category' not in schema['properties']
        return httpx.Response(200, json=completion(wire_response(context)))
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


def substantive_response(payload):
    records = []
    for pair in payload['pairs']:
        facts = pair['facts']
        status = facts['ml2_status']
        text = ('Основание для этого диагноза не найдено; запросите отдельное назначение.'
                if status == 'NO_EVIDENCE' else 'Необходимо сопоставить условия цитаты с диагнозом ' + facts['diagnosis'] + '.')
        records.append(dict(pair_id=pair['pair_id'], category=facts['category'],
            established=[dict(text='Получен результат ML-2: ' + status, fact_refs=['ml2_status'], citation_refs=[])],
            uncertain=[dict(text='Применимость к заявке требует сверки.', fact_refs=['evidence_status'], citation_refs=[])],
            questions=[dict(text=text, why='Без этих сведений применимость не установлена.', fact_refs=['diagnosis', 'ml2_status'], citation_refs=[])],
            citations=pair['citations']))
    return dict(schema_version='staff-analysis.2', recommendations=records)


def test_free_analysis_changes_with_material_facts():
    results = []
    for status in ['NEEDS_REVIEW', 'NO_EVIDENCE']:
        inp, pair = values(status)
        report = recommend_for_staff([inp], [pair], provider=ReportProvider())
        assert report.generation_status == 'MODEL'
        results.append(report.recommendations[0].analysis.questions[0].text)
    assert results[0] != results[1]


@pytest.mark.parametrize('change', ['fact', 'category', 'quote', 'duplicate', 'source', 'extra'])
def test_rejects_fabricated_structured_analysis(change):
    inp, pair = values()
    class Bad:
        def generate(self, payload):
            result = substantive_response(payload)
            rec = result['recommendations'][0]
            if change == 'fact': rec['questions'][0]['fact_refs'] = ['invented']
            if change == 'category': rec['category'] = 'В'
            if change == 'quote': rec['citations'][0]['quote'] = 'Выдумано'
            if change == 'source': rec['questions'][0]['citation_refs'] = ['foreign']
            if change == 'duplicate': result['recommendations'].append(rec)
            if change == 'extra': rec['amount'] = '100'
            return result
    result = recommend_for_staff([inp], [pair], provider=Bad())
    assert result.generation_status == 'FALLBACK'
    assert result.recommendations == recommend_for_staff([inp], [pair]).recommendations


def test_two_diagnoses_keep_separate_facts_and_original_ids():
    inp, pair = values()
    other = inp.model_copy(update={'diagnosis_id': 'd2', 'diagnosis': 'Другой диагноз', 'inn': None, 'ml2_status': 'INN_UNRESOLVED'})
    second = explain_pair(other, verify_ml2_evidence(other, sample()[1]))
    provider = ReportProvider()
    result = recommend_for_staff([inp, other], [pair, second], provider=provider)
    assert result.generation_status == 'MODEL'
    assert result.recommendations[0].pair_id != result.recommendations[1].pair_id
    assert provider.payloads[1]['pairs'][0]['facts']['inn'] is None
    assert provider.payloads[1]['pairs'][0]['facts']['diagnosis'] == 'Другой диагноз'


def test_html_displays_free_analysis_and_escapes_it(tmp_path):
    from llm_rag.excel_scenario import write_report
    from llm_rag.explanation_summary import summarize_explanations
    inp, pair = values('NO_EVIDENCE')
    inp.diagnosis = '<b>Диагноз</b>'
    report = recommend_for_staff([inp], [pair], provider=ReportProvider())
    report.recommendations[0].analysis.established[0].text = '<script>текст</script>'
    app = summarize_explanations([pair], purchases=[])
    html = write_report(tmp_path, 'card', app, {}, [], staff_report=report).read_text(encoding='utf-8')
    assert 'Что установлено' in html and 'Что не подтверждено' in html
    assert 'Почему:' in html and 'Факты:' not in html
    assert '<script>' not in html and '&lt;script&gt;' in html
    assert 'сверки специалистом' in html
    assert 'analysis' in report.model_dump(mode='json')['recommendations'][0]


def test_packet_is_minimal_and_contains_known_limits():
    inp, pair = values()
    inp.context = {'secret': 'PRIVATE'}
    inp.conditions = ['PRIVATE']
    pair.missing_context = ['PRIVATE', 'citation_age_applicability']
    pair.dosage_check.status = 'INVALID'
    provider = ReportProvider()
    report = recommend_for_staff([inp], [pair], provider=provider)
    assert report.generation_status == 'MODEL'
    packet = provider.payload
    assert 'PRIVATE' not in str(packet)
    assert 'allowed_texts' not in str(packet)
    assert packet['pairs'][0]['facts']['dosage_status'] == 'INVALID'
    assert packet['pairs'][0]['facts']['missing_information_count'] == 2
    assert packet['pairs'][0]['facts']['missing_information']


def test_late_failure_keeps_whole_template_without_retry():
    inp, pair = values()
    inputs = [inp.model_copy(update={'item_id': str(i)}) for i in range(3)]
    pairs = [pair.model_copy(update={'item_id': str(i)}) for i in range(3)]
    class FailSecond(ReportProvider):
        calls = 0
        def generate(self, payload):
            self.calls += 1
            if self.calls == 2: raise TimeoutError('PRIVATE')
            return super().generate(payload)
    provider = FailSecond()
    result = recommend_for_staff(inputs, pairs, provider=provider)
    assert provider.calls == 2
    assert result.generation_status == 'FALLBACK'
    assert result.recommendations == recommend_for_staff(inputs, pairs).recommendations


@pytest.mark.parametrize('text', ['Начните терапию этим препаратом. Расходы подлежат оплате полностью.', 'Рекомендуется принимать препарат ежедневно.'])
def test_rejects_explicit_treatment_and_payment_directions(text):
    inp, pair = values()
    class Unsafe(ReportProvider):
        def generate(self, payload):
            result = substantive_response(payload)
            result['recommendations'][0]['questions'][0]['text'] = text
            return result
    assert recommend_for_staff([inp], [pair], provider=Unsafe()).generation_status == 'FALLBACK'


def test_question_may_discuss_strength_already_in_verified_quote():
    inp, pair = values()
    pair.citations[0].quote = 'Форма 50 мг; тяжёлое течение.'
    class Ask(ReportProvider):
        def generate(self, payload):
            result = substantive_response(payload)
            question = result['recommendations'][0]['questions'][0]
            question['text'] = 'Уточните, относится ли концентрация 50 мг из цитаты к приобретённой форме.'
            question['citation_refs'] = ['0']
            return result
    assert recommend_for_staff([inp], [pair], provider=Ask()).generation_status == 'MODEL'


def test_specific_missing_conditions_are_sent_only_with_verified_quote_origin():
    packets = []
    for condition in ['тяжёлое течение', 'недостаточный эффект монотерапии']:
        inp, pair = values()
        pair.citations[0].quote = 'Условия: тяжёлое течение; недостаточный эффект монотерапии.'
        inp.conditions = [condition, 'PRIVATE']
        pair.missing_context = ['condition: ' + condition, 'condition: PRIVATE']
        provider = ReportProvider()
        recommend_for_staff([inp], [pair], provider=provider)
        facts = provider.payload['pairs'][0]['facts']
        assert facts['unverified_conditions'][0]['text'] == condition
        assert facts['unverified_conditions'][0]['citation_ref'] == '0'
        assert 'PRIVATE' not in str(facts)
        packets.append(facts)
    assert packets[0] != packets[1]


def test_excel_pipeline_does_not_call_quote_reordering_provider(monkeypatch):
    from types import SimpleNamespace
    import llm_rag.excel_scenario as scenario
    inp, snap = sample()
    batch = SimpleNamespace(request=SimpleNamespace(request_id='r', items=[]), details=lambda: {})
    monkeypatch.setattr(scenario, 'ML2Pipeline', lambda *a, **k: SimpleNamespace(run=lambda *a, **k: batch))
    monkeypatch.setattr(scenario, 'prepare_explanation_inputs', lambda *a, **k: [inp])
    class NoPaidReordering:
        def generate(self, payload):
            pytest.fail('Отдельный вызов ради перестановки цитат')
    app, _, _ = scenario.process_card(SimpleNamespace(request=None, diagnosis_id='d'), None,
        SimpleNamespace(snapshot=lambda i: snap), None, NoPaidReordering())
    assert app.pairs[0].citations == inp.citations


def test_fallback_diagnostics_identify_validation_stage_without_raw_input():
    inp, pair = values()
    class Broken(ReportProvider):
        def generate(self, payload):
            return {'recommendations': 'PRIVATE', 'token': 'SECRET'}
    report = recommend_for_staff([inp], [pair], provider=Broken())
    assert report.generation_status == 'FALLBACK'
    diagnostic = report.diagnostics[0]
    assert diagnostic['stage'] == 'response_validation'
    assert diagnostic['code'] == 'STAFF_INVALID_SCHEMA'
    assert 'PRIVATE' not in str(diagnostic) and 'SECRET' not in str(diagnostic)
    assert diagnostic['traceback']


def test_fallback_diagnostics_distinguish_unknown_reference():
    inp, pair = values()
    class Broken(ReportProvider):
        def generate(self, payload):
            result = substantive_response(payload)
            result['recommendations'][0]['questions'][0]['fact_refs'] = ['PRIVATE']
            return result
    report = recommend_for_staff([inp], [pair], provider=Broken())
    assert report.diagnostics[0]['code'] == 'STAFF_UNKNOWN_REFERENCE'
    assert 'PRIVATE' not in str(report.diagnostics)


def test_diagnostic_failure_cannot_break_fallback():
    inp, pair = values()
    class UnprintableError(Exception):
        def __str__(self):
            raise RuntimeError('SECRET')
    class Broken:
        def generate(self, payload):
            raise UnprintableError()
    report = recommend_for_staff([inp], [pair], provider=Broken())
    assert report.generation_status == 'FALLBACK'
    assert report.diagnostics[0]['code'] == 'UNEXPECTED_ERROR'
    assert 'SECRET' not in str(report.diagnostics)


def wire_response(context):
    packet = context['pairs'][0]
    return dict(pair_id=packet['pair_id'],
        uncertain='Клиническая применимость требует сверки.', question='Есть ли документ отдельного назначения?',
        why='Для сопоставления основания с этой покупкой.', fact_refs=['ml2_status'], citation_refs=[])


@pytest.mark.parametrize('change', ['extra_category', 'wrong_pair', 'too_long', 'empty', 'unknown_fact'])
def test_sdk_compact_wire_keeps_local_validation_strict(sdk_wire, change):
    import httpx, json
    def handler(request):
        if request.url.path == '/api/v2/oauth':
            return httpx.Response(200, json={'access_token': 'test-access', 'expires_at': 1_900_000_000_000})
        context = json.loads(json.loads(request.content)['messages'][1]['content'])
        wire = wire_response(context)
        if change == 'extra_category': wire['category'] = 'В'
        if change == 'wrong_pair': wire['pair_id'] = 'other'
        if change == 'too_long': wire['question'] = 'x' * 2001
        if change == 'empty': wire['uncertain'] = ''
        if change == 'unknown_fact': wire['fact_refs'] = ['invented']
        return httpx.Response(200, json=completion(wire))
    sdk_wire.handler = handler
    inp, pair = values()
    with make_provider() as provider:
        report = recommend_for_staff([inp], [pair], provider=provider)
    assert report.generation_status == 'FALLBACK'
    assert report.recommendations == recommend_for_staff([inp], [pair]).recommendations


def test_wire_schema_constrains_citation_indices_for_each_pair():
    from llm_rag.staff_contracts import StaffFactPair, staff_wire_schema
    inp, pair = values()
    packet = StaffFactPair(pair_id='pair-0', facts={'ml2_status': 'PROPOSED'}, citations=pair.citations)
    assert staff_wire_schema(packet)['properties']['citation_refs']['items']['enum'] == ['0']
    packet.citations = []
    assert staff_wire_schema(packet)['properties']['citation_refs']['maxItems'] == 0


def test_sdk_calls_and_counter_match_one_request_per_pair(sdk_wire):
    import httpx, json
    from llm_rag.excel_scenario import CallCounter
    calls = []
    def handler(request):
        if request.url.path == '/api/v2/oauth':
            return httpx.Response(200, json={'access_token': 'test-access', 'expires_at': 1_900_000_000_000})
        context = json.loads(json.loads(request.content)['messages'][1]['content'])
        assert len(context['pairs']) == 1
        calls.append(context['pairs'][0]['pair_id'])
        return httpx.Response(200, json=completion(wire_response(context)))
    sdk_wire.handler = handler
    inp, pair = values()
    inputs = [inp, inp.model_copy(update={'item_id': 'other'})]
    pairs = [pair, pair.model_copy(update={'item_id': 'other'})]
    with make_provider() as provider:
        counter = CallCounter(provider)
        report = recommend_for_staff(inputs, pairs, provider=counter)
    assert report.generation_status == 'MODEL'
    assert len(calls) == counter.stats['calls'] == counter.stats['responses'] == 2
    assert counter.stats['tokens'] == 280
    for record, source in zip(report.recommendations, pairs):
        assert record.analysis.citations == source.citations
        assert record.analysis.category == source.category


@pytest.mark.parametrize('text', ['Диагноз Бронхиальная астма подтвержден.', 'Назначен препарат Сальбутамол для лечения астмы.', 'Отсутствие статуса ML2 требует уточнения.'])
def test_rejects_false_confirmation_prescription_and_missing_status(text):
    inp, pair = values('NO_EVIDENCE')
    class Broken(ReportProvider):
        def generate(self, payload):
            result = substantive_response(payload)
            result['recommendations'][0]['established'][0]['text'] = text
            return result
    report = recommend_for_staff([inp], [pair], provider=Broken())
    assert report.generation_status == 'FALLBACK'


@pytest.mark.parametrize('text', ['Диагноз не был подтвержден документами.', 'Не назначен препарат по представленным документам.', 'Есть ли документ, где назначен препарат врачом?'])
def test_fact_filter_allows_negation_and_document_questions(text):
    inp, pair = values()
    class Valid(ReportProvider):
        def generate(self, payload):
            result = substantive_response(payload)
            result['recommendations'][0]['uncertain'][0]['text'] = text
            return result
    assert recommend_for_staff([inp], [pair], provider=Valid()).generation_status == 'MODEL'
