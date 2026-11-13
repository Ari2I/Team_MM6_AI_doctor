import json

from llm_rag.explanation_summary import summarize_explanations
from llm_rag.staff_report import recommend_for_staff
from tests.test_staff_report import values, ReportProvider


def test_human_report_translates_technical_text_and_preserves_evidence(tmp_path):
    from llm_rag.human_report import build_human_report, render_human_report
    inp, pair = values('PROPOSED')
    report = recommend_for_staff([inp], [pair], provider=ReportProvider())
    report.recommendations[0].analysis.established[0].text = 'статус ML-2: PROPOSED; diagnosis, inn, category, ml2_status.'
    report.recommendations[0].analysis.questions[0].why = 'Нужна проверка clinical_applicability и context.age.'
    app = summarize_explanations([pair], purchases=[])
    human = build_human_report('Карточка', app, [], staff_report=report)
    text = json.dumps(human, ensure_ascii=False)
    for raw in ['PROPOSED', 'ML-2', 'clinical_applicability', 'context.age', 'fact_refs', 'citation_refs', 'ml2_status']:
        assert raw not in text
    assert human['items'][0]['sources'][0]['quote'] == pair.citations[0].quote
    assert human['items'][0]['sources'][0]['url'] == pair.citations[0].url
    html = render_human_report(human)
    assert 'Факты:' not in html and 'Технические статусы' not in html
    assert 'Обращения к модели' not in html and '<pre>' not in html
    assert pair.citations[0].quote in html


def test_human_view_does_not_mutate_internal_result_and_explains_fallback():
    from llm_rag.human_report import build_human_report
    inp, pair = values('NO_EVIDENCE')
    report = recommend_for_staff([inp], [pair], provider=ReportProvider('timeout'))
    before = report.model_dump_json()
    human = build_human_report('Карточка', summarize_explanations([pair], purchases=[]), [], staff_report=report)
    assert report.model_dump_json() == before
    assert 'резервные' in human['recommendation_note']
    assert 'сопутствующий диагноз' in ' '.join(human['items'][0]['actions'])
    assert human['items'][0]['category'] == (pair.category or 'не установлена')


def test_html_is_rendered_from_public_view_and_escapes_text():
    from llm_rag.human_report import build_human_report, render_human_report
    inp, pair = values()
    pair.comment = '<script>alert(1)</script>'
    human = build_human_report('<input>', summarize_explanations([pair], purchases=[]), [])
    html = render_human_report(human)
    assert '<script>' not in html and '&lt;script&gt;' in html
    assert '&lt;input&gt;' in html


def test_public_references_distinguish_quotes_from_same_document():
    from llm_rag.human_report import build_human_report
    inp, pair = values()
    pair.citations.append(pair.citations[0].model_copy(update={'fragment_id': 'other', 'quote': 'Другая цитата.'}))
    report = recommend_for_staff([inp], [pair], provider=ReportProvider())
    a = report.recommendations[0].analysis
    a.established[0].citation_refs = ['0']
    a.questions[0].citation_refs = ['1']
    human = build_human_report('Карточка', summarize_explanations([pair], purchases=[]), [], staff_report=report)
    first=human['items'][0]['established'][0]['sources'][0]
    second=human['items'][0]['questions'][0]['sources'][0]
    assert first['number'] != second['number']
    assert first['quote'] == pair.citations[0].quote
    assert second['quote'] == pair.citations[1].quote


def test_internal_missing_evidence_markers_are_not_public():
    from llm_rag.human_report import build_human_report, render_human_report
    inp, pair = values()
    pair.comment = 'Недостающие сведения: snapshot: fragment123, ml2_details.request_id, classification.status, context.some_key.'
    human = build_human_report('Карточка', summarize_explanations([pair], purchases=[]), [])
    text = render_human_report(human)
    for marker in ['snapshot:', 'fragment123', 'ml2_details', 'classification.', 'request_id', 'context.', 'some_key']:
        assert marker not in text
