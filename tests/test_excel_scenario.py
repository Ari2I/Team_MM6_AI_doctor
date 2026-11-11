from decimal import Decimal
from zipfile import ZipFile
import xml.etree.ElementTree as ET

import pytest

from tests.helpers import ROOT
from llm_rag.excel_scenario import read_card, write_report, collect_files


CARD = ROOT / 'excel_input/01_Подходящие_препараты.xlsx'


def mutated_card(tmp_path, cell, value, *, sheet='sheet2.xml'):
    output = tmp_path / 'changed.xlsx'
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    with ZipFile(CARD) as source, ZipFile(output, 'w') as target:
        for item in source.infolist():
            raw = source.read(item.filename)
            if item.filename == 'xl/worksheets/' + sheet:
                tree = ET.fromstring(raw)
                node = tree.find(f'.//{{{ns}}}c[@r="{cell}"]')
                for child in list(node):
                    node.remove(child)
                node.set('t', 'inlineStr')
                ET.SubElement(ET.SubElement(node, f'{{{ns}}}is'), f'{{{ns}}}t').text = value
                raw = ET.tostring(tree)
            target.writestr(item, raw)
    return output


def test_card_is_read_without_cwd_dependency(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    card = read_card(CARD)
    assert card.request.diagnosis == 'Бронхиальная астма'
    assert card.request.context['icd10'] == 'J45.9'
    assert card.request.context['age'] == '45'
    assert [i.original.trade_name for i in card.request.items] == ['Сальбутамол', 'Будесонид']
    assert sum(i.original.amount for i in card.request.items) == Decimal('950')
    assert card.request.items[1].original.quantity == 2


def test_age_is_optional_and_never_inferred(tmp_path):
    card = read_card(mutated_card(tmp_path, 'B6', '', sheet='sheet1.xml'))
    assert 'age' not in card.request.context
    assert 'age_group' not in card.request.context


@pytest.mark.parametrize('cell,value', [('A3', 'P1'), ('E2', '-1'), ('E2', ''), ('D2', '1.5'), ('B1', 'Неверный заголовок')])
def test_invalid_card_rejected_with_specific_reason(tmp_path, cell, value):
    with pytest.raises(ValueError):
        read_card(mutated_card(tmp_path, cell, value))


def test_formula_is_not_executed_or_silently_cached(tmp_path):
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    path = mutated_card(tmp_path, 'E2', '300')
    target = tmp_path / 'formula.xlsx'
    with ZipFile(path) as source, ZipFile(target, 'w') as output:
        for item in source.infolist():
            raw = source.read(item.filename)
            if item.filename == 'xl/worksheets/sheet2.xml':
                tree = ET.fromstring(raw)
                cell = tree.find(f'.//{{{ns}}}c[@r="E2"]')
                ET.SubElement(cell, f'{{{ns}}}f').text = '1+1'
                raw = ET.tostring(tree)
            output.writestr(item, raw)
    with pytest.raises(ValueError, match='формул'):
        read_card(target)


def test_lock_files_are_ignored(tmp_path):
    (tmp_path / '~$opened.xlsx').write_bytes(b'lock')
    (tmp_path / 'one.xlsx').write_bytes(b'input')
    assert [p.name for p in collect_files(tmp_path)] == ['one.xlsx']


def test_negative_card_is_separate_external_input():
    card = read_card(ROOT / 'excel_input/02_Препарат_другого_назначения.xlsx')
    assert card.request.context['icd10'] == 'J45.9'
    assert card.request.items[1].original.trade_name == 'Метформин'
    assert sum(i.original.amount for i in card.request.items) == Decimal('550')


def test_parsing_uses_captured_bytes_even_if_original_changes(tmp_path):
    captured = CARD.read_bytes()
    changed = tmp_path / 'changing.xlsx'
    changed.write_bytes(b'changed after capture')
    card = read_card(changed, content=captured)
    assert card.request.request_id == 'TEST-REAL-001'
    assert card.request.items[0].original.trade_name == 'Сальбутамол'


def test_directory_script_handles_bad_excel_without_api(tmp_path):
    import os
    import subprocess
    import sys
    inbox = tmp_path / 'inbox'
    inbox.mkdir()
    (inbox / 'broken.xlsx').write_bytes(b'not a workbook')
    completed = subprocess.run([sys.executable, str(ROOT / 'run_excel_scenario.py'),
        '--input-dir', str(inbox), '--output-dir', str(tmp_path / 'reports')], cwd=tmp_path,
        env=dict(os.environ), capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert completed.returncode == 2
    assert 'ОШИБКА ВХОДА' in completed.stdout
    assert 'ГРЛС:' not in completed.stdout


def test_malformed_xml_is_rejected_and_file_is_closed(tmp_path):
    import os
    import subprocess
    import sys
    inbox = tmp_path / 'inbox'
    inbox.mkdir()
    bad = inbox / 'corrupt.xlsx'
    with ZipFile(CARD) as source, ZipFile(bad, 'w') as target:
        for item in source.infolist():
            target.writestr(item, b'<broken' if item.filename == 'xl/workbook.xml' else source.read(item.filename))
    completed = subprocess.run([sys.executable, str(ROOT / 'run_excel_scenario.py'),
        '--input-dir', str(inbox), '--output-dir', str(tmp_path / 'out')], cwd=tmp_path,
        env=dict(os.environ), capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert completed.returncode == 2
    assert 'ОШИБКА ВХОДА' in completed.stdout
    with pytest.raises(Exception):
        read_card(bad)
    bad.unlink()
    assert not bad.exists()


@pytest.mark.parametrize('tls_args,verify', [([], False), (['--verify-ssl'], True)])
@pytest.mark.parametrize('file_scope,environment_scope,expected_scope', [
    (None, None, 'GIGACHAT_API_B2B'),
    ('GIGACHAT_API_PERS', None, 'GIGACHAT_API_PERS'),
    ('GIGACHAT_API_B2B', 'GIGACHAT_API_PERS', 'GIGACHAT_API_PERS'),
    ('GIGACHAT_API_PERS', 'GIGACHAT_API_B2B', 'GIGACHAT_API_B2B'),
])
def test_bad_file_does_not_prevent_processing_good_file(
    tmp_path, monkeypatch, tls_args, verify, file_scope, environment_scope, expected_scope
):
    import importlib.util
    import json
    from types import SimpleNamespace
    from hashlib import sha256
    from llm_rag.evidence_snapshot import EvidenceSnapshot
    from llm_rag.explanation import explain_pairs
    from llm_rag.explanation_contracts import prepare_explanation_inputs, PurchaseAmount
    from llm_rag.explanation_summary import summarize_explanations
    spec = importlib.util.spec_from_file_location('excel_checker', ROOT / 'run_excel_scenario.py')
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    inbox = tmp_path / 'inbox'
    inbox.mkdir()
    (inbox / '01_bad.xlsx').write_bytes(b'corrupt')
    raw = CARD.read_bytes()
    (inbox / '02_good.xlsx').write_bytes(raw)
    monkeypatch.setattr(checker, 'RegistrySnapshot', SimpleNamespace(load=lambda p: SimpleNamespace(version='test', registrations=[])))
    monkeypatch.setattr(checker, 'dotenv_values', lambda *a, **kw:
        {'GIGACHAT_SCOPE': file_scope} if file_scope else {})
    monkeypatch.delenv('GIGACHAT_SCOPE', raising=False)
    if environment_scope:
        monkeypatch.setenv('GIGACHAT_SCOPE', environment_scope)
    provider_options = []
    class Provider:
        def __init__(self, **kwargs):
            provider_options.append(kwargs)
            self.usage_totals = {'total_tokens': 0}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def generate(self, payload):
            return dict(item_id=payload['item']['item_id'], claims=[
                {k: c[k] for k in ('claim_id', 'text', 'fragment_id', 'quote')} for c in payload['evidence']])
    monkeypatch.setattr(checker, 'ML2GigaChatProvider', Provider)
    monkeypatch.setattr(checker, 'GigaChatProvider', Provider)
    def process(card, *args):
        inputs = prepare_explanation_inputs(card.request, diagnosis_id=card.diagnosis_id, ml2_details={})
        pairs = explain_pairs(inputs, EvidenceSnapshot(snapshot_version='test'))
        app = summarize_explanations(pairs, purchases=[PurchaseAmount(request_id=card.request.request_id,
            item_id=i.item_id, amount=i.original.amount, currency=i.original.currency) for i in card.request.items])
        # Файл изменяется во время обработки: SHA обязан остаться от прочитанного входа.
        (inbox / '02_good.xlsx').write_bytes(b'new version')
        return app, SimpleNamespace(request=card.request, details=lambda: {}), []
    monkeypatch.setattr(checker, 'process_card', process)
    registry = tmp_path / 'registry.json'
    registry.write_text('{}')
    assert checker.main(['--input-dir', str(inbox), '--output-dir', str(tmp_path / 'reports'), '--registry', str(registry), *tls_args]) == 2
    assert len(provider_options) == 2
    assert all(options['verify_ssl_certs'] is verify for options in provider_options)
    assert all(options['scope'] == expected_scope for options in provider_options)
    records = list((tmp_path / 'reports').glob('*/001/result.json'))
    assert len(records) == 1
    assert json.loads(records[0].read_text(encoding='utf-8'))['input_sha256'] == sha256(raw).hexdigest()


def test_report_escapes_input_and_keeps_unknown_visible(tmp_path):
    from tests.test_evidence_snapshot import sample
    from llm_rag.evidence_snapshot import verify_ml2_evidence
    from llm_rag.explanation import explain_pair
    from llm_rag.explanation_summary import summarize_explanations
    inp, snap = sample()
    result = summarize_explanations([explain_pair(inp, verify_ml2_evidence(inp, snap))], purchases=[])
    result.pairs[0].comment = '<script>alert(1)</script>'
    output = write_report(tmp_path, '<input>', result, {}, [])
    html = output.read_text(encoding='utf-8')
    assert '<script>' not in html
    assert '&lt;script&gt;' in html
    assert 'Неизвестная сумма' in html
    assert 'Классификация по клиническим рекомендациям' in html
    assert 'предварительн' not in html.casefold()


def test_quotes_appear_once_with_one_source_heading(tmp_path):
    from tests.test_evidence_snapshot import sample
    from llm_rag.evidence_snapshot import verify_ml2_evidence
    from llm_rag.explanation import explain_pair
    from llm_rag.explanation_summary import summarize_explanations
    inp, snap = sample()
    evidence = verify_ml2_evidence(inp, snap)
    evidence.citations.append(evidence.citations[0].model_copy(update={'fragment_id': 'second', 'quote': 'Второе извлечение.'}))
    app = summarize_explanations([explain_pair(inp, evidence)], purchases=[])
    html = write_report(tmp_path, 'card', app, {}, []).read_text(encoding='utf-8')
    assert html.count('Разрешённый текст.') == 1
    assert html.count('Второе извлечение.') == 1
    assert html.count('Синтетический источник') == 1
    assert 'Применимость к заявителю' in html
