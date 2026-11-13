"""Регрессии импорта и доверенного получения клинических текстов, без сети."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from io import BytesIO
import base64
import gzip
import json
from zipfile import ZipFile
import xml.etree.ElementTree as ET

import httpx
import pytest

from tests.helpers import ROOT
from llm_rag.excel_scenario import read_card
from llm_rag.ml2 import clinicalrecs as cr


HTML = '''<h1>Синтетическая КР</h1><dl class="krt-pass">
<dt>ID:</dt><dd>1_1</dd><dt>Кодирование по МКБ-10:</dt><dd>J45</dd>
<dt>Возрастная категория:</dt><dd>Взрослые, дети</dd>
<dt>Год утверждения:</dt><dd>2024</dd><dt>Статус:</dt><dd>Действует</dd>
<dt>Статус применения:</dt><dd>Применяется</dd></dl>
<p class="krt-srcl">Источник: Рубрикатор Минздрава России</p>
<a href="https://cr.minzdrav.gov.ru/preview-cr/1_1">Оригинал</a>
<h2 id="s3">3. Лечение</h2><ul id="r1" class="krt-r"><li>Сальбутамол рекомендован.</li></ul>
<h2 id="s4">4. Реабилитация</h2>'''


def test_stale_dimensions_do_not_drop_purchases():
    path = ROOT / 'excel_input/01_Подходящие_препараты.xlsx'
    ns = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
    content = BytesIO()
    with ZipFile(path) as source, ZipFile(content, 'w') as target:
        for item in source.infolist():
            raw = source.read(item.filename)
            if item.filename == 'xl/worksheets/sheet2.xml':
                tree = ET.fromstring(raw)
                dim = tree.find(ns + 'dimension')
                if dim is None:
                    dim = ET.Element(ns + 'dimension')
                    tree.insert(0, dim)
                dim.set('ref', 'A1:F2')
                raw = ET.tostring(tree)
            target.writestr(item, raw)
    actual = read_card(path, content=content.getvalue()).request
    assert len(actual.items) == 2
    assert sum(i.original.amount for i in actual.items) == 950


def catalog_html():
    data = {'RU': [['Синтетическая КР', '1_1', '2024', 3, 3, [], 'J45', [], [], '', '']]}
    packed = base64.b64encode(gzip.compress(json.dumps(data).encode())).decode()
    html = '<script>const KRP = {"1":"test-1"}; const krPage = x => x;</script>'
    return html + ''.join(f'<script id="krdata{i}">{packed[i*len(packed)//11:(i+1)*len(packed)//11]}</script>' for i in range(11))


@pytest.mark.parametrize('catalog', [False, True])
@pytest.mark.parametrize('final_status', [200, 304])
def test_http_redirect_rejected_before_request_even_with_cache(monkeypatch, tmp_path, catalog, final_status):
    real_client = httpx.Client
    url = cr.BASE_URL + ('/' if catalog else '/kr/test-1')
    checked = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    if catalog:
        (tmp_path / 'catalog-ru.json').write_text(json.dumps({'html': catalog_html(), 'checked_at': checked}), encoding='utf-8')
    else:
        html_path, meta_path = cr._cache_files(tmp_path, url)
        from hashlib import sha256
        html_path.write_text(HTML, encoding='utf-8')
        meta_path.write_text(json.dumps({'checked_at': checked, 'sha256': sha256(HTML.encode()).hexdigest()}), encoding='utf-8')
    seen = []
    def handler(request):
        seen.append(request.url)
        if request.url.path == '/robots.txt':
            return httpx.Response(200, text='User-agent: *\nAllow: /')
        if request.url.scheme == 'https':
            return httpx.Response(302, headers={'location': str(request.url).replace('https:', 'http:')})
        return httpx.Response(final_status, text=HTML, headers={'content-type': 'text/html'})
    monkeypatch.setattr(cr.httpx, 'Client', lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    if catalog:
        result = cr.recommendations_for_diagnosis('J45', cache_dir=tmp_path)
        assert result[0].cache_state == 'STALE_CACHE'
    else:
        result = cr.fetch_recommendation(url, cache_dir=tmp_path)
        assert result.cache_state == 'STALE_FALLBACK'
    assert all(url.scheme == 'https' for url in seen)


def test_dense_mentions_keep_complete_bounded_sentences():
    rec = cr.parse_recommendation(HTML, cr.BASE_URL + '/kr/test-1')
    sentences = [f'Условие {i}: ' + 'а' * 900 + '. Сальбутамол рекомендован.' for i in range(30)]
    text = ' '.join(sentences)
    rec = replace(rec, segments=(cr.TreatmentSegment('long', text),))
    fragments = cr.fragments_for_inn(rec, 'Сальбутамол', window_chars=3000)
    assert fragments
    assert len(fragments) <= 20
    assert all(len(f.text) <= 3000 for f in fragments)
    assert all(f.text in text and (text.index(f.text) == 0 or text[text.index(f.text)-2:text.index(f.text)] == '. ') for f in fragments)
    assert all(f.text.endswith('.') for f in fragments)


def test_oversized_single_sentence_is_not_cut_into_assertion():
    rec = cr.parse_recommendation(HTML, cr.BASE_URL + '/kr/test-1')
    rec = replace(rec, segments=(cr.TreatmentSegment('long', 'Не применяют ' + 'а' * 4000 + ' сальбутамол.'),))
    with pytest.raises(cr.ClinicalSourceError, match='(?i)предложение'):
        cr.fragments_for_inn(rec, 'Сальбутамол')


@pytest.mark.parametrize('short_count', [1, 25])
def test_short_positive_evidence_does_not_hide_oversized_negative(short_count):
    rec = cr.parse_recommendation(HTML, cr.BASE_URL + '/kr/test-1')
    segments = [cr.TreatmentSegment(f'short-{i}', f'В ситуации {i} сальбутамол рекомендован.')
                for i in range(short_count)]
    segments.append(cr.TreatmentSegment('contraindication', 'Сальбутамол противопоказан при ' + 'а' * 4000 + '.'))
    rec = replace(rec, segments=tuple(segments))
    with pytest.raises(cr.ClinicalSourceError, match='(?i)предложение'):
        cr.fragments_for_inn(rec, 'Сальбутамол')


def test_oversized_source_is_processing_error_not_no_evidence():
    from datetime import date
    from types import SimpleNamespace
    from llm_rag.ml2.registry import RegistrySnapshot, RegistryEntry, RegistrySource
    from llm_rag.ml2.pipeline import ML2Pipeline
    card = read_card(ROOT / 'excel_input/01_Подходящие_препараты.xlsx')
    source = RegistrySource(title='Учебный реестр', url='https://grls.rosminzdrav.ru/GRLS.aspx',
        version='1', location='строка 1', retrieved_at=date(2026, 11, 12), quote='Учебная запись.')
    registry = RegistrySnapshot(version='test', as_of=date(2026, 11, 12), registrations=(
        RegistryEntry(registration='1', trade_name='Сальбутамол', mnn='Сальбутамол', source=source),))
    rec = cr.parse_recommendation(HTML, cr.BASE_URL + '/kr/test-1')
    rec = replace(rec, segments=(cr.TreatmentSegment('long', 'Не применяют ' + 'а' * 4000 + ' сальбутамол.'),))
    candidate = cr.CatalogCandidate(title=rec.title, edition_id=rec.edition_id, published_at='2024',
        population=rec.population, icd10=rec.icd10, page_url=rec.page_url, primary_url=rec.primary_url)
    sources = SimpleNamespace(candidates=lambda *a: (candidate,),
        load=lambda _: cr.CachedRecommendation(rec, 'FRESH_CACHE', datetime.now(UTC).isoformat()))
    batch = ML2Pipeline(registry, sources=sources).run(card.request, live_sources=True)
    assert batch.evaluations[0].classification.status == 'SOURCE_ERROR'


@pytest.mark.parametrize('quote,age,age_marker', [
    ('Рекомендован детям до 5 лет.', '45', True),
    ('Рекомендован детям в возрасте 6–11 лет.', '45', True),
    ('Рекомендован детям младше 6 лет.', '45', True),
    ('Рекомендован пациентам старше 65 лет.', '45', True),
    ('Рекомендован взрослым при тяжёлом течении.', '45', False),
])
def test_live_applicability_never_verified_from_text_match_alone(monkeypatch, quote, age, age_marker):
    from types import SimpleNamespace
    from tests.test_evidence_snapshot import sample
    from llm_rag import excel_scenario as excel
    inp, snapshot = sample()
    inp.context['age'] = age
    inp.citations[0].quote = snapshot.documents[0].text = quote
    original_category = inp.category
    card = read_card(ROOT / 'excel_input/01_Подходящие_препараты.xlsx')
    batch = SimpleNamespace(request=card.request, details=lambda: {})
    monkeypatch.setattr(excel, 'ML2Pipeline', lambda *a, **k: SimpleNamespace(run=lambda *a, **k: batch))
    monkeypatch.setattr(excel, 'prepare_explanation_inputs', lambda *a, **k: [inp])
    monkeypatch.setattr(excel, 'summarize_explanations', lambda pairs, **k: SimpleNamespace(pairs=pairs))
    sources = SimpleNamespace(snapshot=lambda _: snapshot)
    app, _, _ = excel.process_card(card, None, sources, None, None)
    pair = app.pairs[0]
    assert pair.evidence_status == 'INSUFFICIENT_DATA'
    assert pair.category == original_category
    assert pair.citations[0].quote == quote
    assert 'clinical_applicability' in pair.missing_context
    assert ('citation_age_applicability' in pair.missing_context) is age_marker
