from decimal import Decimal
from zipfile import ZipFile
import xml.etree.ElementTree as ET

import pytest

from tests.helpers import ROOT
from llm_rag.excel_scenario import read_card, write_report, collect_files


CARD = ROOT / 'excel_input/01_Подходящие_препараты.xlsx'


def mutated_card(tmp_path, cell, value, *, sheet='sheet2.xml', numeric=False):
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
                if numeric:
                    node.set('t', 'n')
                    ET.SubElement(node, f'{{{ns}}}v').text = value
                else:
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


@pytest.mark.parametrize('age', [0, 130])
def test_legacy_age_boundaries(tmp_path, age):
    card = read_card(mutated_card(tmp_path, 'B6', str(age), sheet='sheet1.xml'))
    assert card.request.context['age'] == str(age)


@pytest.mark.parametrize('quantity', [1, 1_000_000])
def test_legacy_quantity_boundaries(tmp_path, quantity):
    card = read_card(mutated_card(tmp_path, 'D2', str(quantity)))
    assert card.request.items[0].original.quantity == quantity


@pytest.mark.parametrize('value', ['-1', '131', '10.5'])
def test_legacy_age_out_of_range(tmp_path, value):
    with pytest.raises(ValueError, match='Возраст'):
        read_card(mutated_card(tmp_path, 'B6', value, sheet='sheet1.xml'))


@pytest.mark.parametrize('value', ['0', '1000001', '1e8'])
def test_legacy_quantity_out_of_range(tmp_path, value):
    with pytest.raises(ValueError, match='Количество'):
        read_card(mutated_card(tmp_path, 'D2', value))


@pytest.mark.parametrize('sheet,cell,value,label', [
    ('sheet1.xml', 'B6', '130.000000000000001', 'Возраст'),
    ('sheet1.xml', 'B6', '44.999999999999999', 'Возраст'),
    ('sheet2.xml', 'D2', '1000000.000000000001', 'Количество'),
    ('sheet2.xml', 'D2', '1.99999999999999999', 'Количество'),
])
def test_legacy_numeric_cells_checked_at_xml_precision(tmp_path, sheet, cell, value, label):
    with pytest.raises(ValueError, match=label):
        read_card(mutated_card(tmp_path, cell, value, sheet=sheet, numeric=True))


@pytest.mark.parametrize('value,limits', [('131', (0, 130)), ('1000001', (1, 1_000_000))])
def test_legacy_limits_checked_before_integer_conversion(monkeypatch, value, limits):
    from llm_rag import excel_scenario

    def premature_conversion(number):
        pytest.fail('Число вне диапазона передано в int')

    monkeypatch.setattr(excel_scenario, 'int', premature_conversion, raising=False)
    with pytest.raises(ValueError):
        excel_scenario._number(value, 'Поле', integer_bounds=limits)


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
