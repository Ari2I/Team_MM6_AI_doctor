"""Публичное представление результата: понятные тексты без журнала и внутренних ключей."""

from html import escape
import re

from llm_rag.explanation_contracts import pair_key


STATUS_LABELS = {
    'PROPOSED': 'категория определена автоматически',
    'NEEDS_CONTEXT': 'не хватает сведений для проверки',
    'NO_EVIDENCE': 'основание для указанного диагноза не найдено',
    'INN_UNRESOLVED': 'действующее вещество не определено',
    'MULTI_INGREDIENT_REVIEW': 'состав требует проверки специалистом',
    'CONFLICT': 'обнаружены противоречащие основания',
    'NEEDS_REVIEW': 'требуется дополнительная сверка',
    'INVALID_RESPONSE': 'не удалось получить проверяемое заключение',
    'SOURCE_ERROR': 'источник недоступен', 'PROVIDER_ERROR': 'проверку не удалось завершить',
    'VERIFIED': 'текст и происхождение основания проверены в указанном объёме',
    'INSUFFICIENT_DATA': 'не хватает сведений для проверки применимости',
    'UNCONFIRMED': 'основание категории не подтверждено',
    'SOURCE_CONFLICT': 'есть конфликт оснований', 'PROCESSING_ERROR': 'проверку не удалось завершить',
    'APPROVED_RULE': 'согласованное основание', 'ML2_PROPOSAL': 'результат автоматической классификации',
    'NONE': 'основание не установлено', 'NOT_CHECKED': 'не проверялось',
    'VALID': 'соответствие подтверждено', 'INVALID': 'обнаружено несоответствие',
}
FIELD_LABELS = {
    'context.age_group': 'возрастная группа', 'context.age': 'возраст заявителя',
    'citation_age_applicability': 'возрастные условия цитаты требуют отдельной сверки',
    'clinical_applicability': 'применимость клинических условий независимо не проверена',
    'classification_basis': 'основание классификации', 'evidence_status': 'проверка основания',
    'ml2_status_meaning': 'результат автоматической проверки', 'ml2_status': 'результат автоматической проверки',
    'ML2_STATUS': 'результат автоматической проверки', 'dosage_status': 'проверка дозировки',
    'dosage_check': 'проверка дозировки', 'age_provided': 'наличие сведений о возрасте',
    'missing_information': 'недостающие сведения', 'diagnosis': 'диагноз из заявки',
    'inn': 'действующее вещество', 'category': 'категория', 'condition:': 'условие применимости:',
}


def readable_text(value):
    """Перевести служебные обозначения в пояснения, не изменяя исходное заключение."""
    text = value
    text = re.sub(r'snapshot:\s*[^,\n.]+', 'фрагмент источника отсутствует в сохранённых материалах', text)
    text = re.sub(r'ml2_details(?:\.[A-Za-z_][\w.]*)?', 'подробные результаты автоматической проверки', text)
    text = re.sub(r'classification\.[A-Za-z_][\w.]*', 'сведения о результате классификации', text)
    text = re.sub(r'статус ML[- ]?2:', 'результат автоматической проверки:', text, flags=re.I)
    text = text.replace('ML-2 присвоил категорию', 'Система определила категорию')
    text = text.replace('Категория ML-2 не установлена', 'Категория не установлена')
    text = text.replace('ML-2 не передал основание', 'Основание для категории не найдено')
    text = re.sub(r'присвоена ML[- ]?2', 'определена автоматически', text)
    for raw, label in sorted({**STATUS_LABELS, **FIELD_LABELS}.items(), key=lambda entry: -len(entry[0])):
        text = re.sub(r'(?<!\w)' + re.escape(raw) + r'(?!\w)', lambda _: label, text)
    text = re.sub(r'\bML[- ]?2\b', 'автоматическая проверка', text, flags=re.I)
    text = re.sub(r'context\.[A-Za-z_][\w.]*', 'дополнительные сведения о заявителе', text)
    text = re.sub(r'\b[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+\b', 'сведения для дополнительной проверки', text)
    return text


def build_human_report(name, app, items, *, staff_report=None, diagnosis=None):
    """Построить разрешённый для интерфейса раздел; внутренние модели не менять."""
    labels = {item.item_id: item for item in items}
    recommendations = {r.pair_id: r for r in staff_report.recommendations} if staff_report else {}
    rows = []
    for number, pair in enumerate(app.pairs, 1):
        item = labels.get(pair.item_id)
        rec = recommendations.get(pair_key(pair))
        sources = [dict(number=index, location=re.sub(r'\s*/\s*r\d+(?:-\d+)*$', '', c.location), title=c.title or 'Клинические рекомендации', version=c.version,
                        url=c.url, copy_url=c.copy_url, quote=c.quote) for index, c in enumerate(pair.citations, 1)]
        def statement(value):
            refs = list(dict.fromkeys(value.citation_refs))
            result = dict(text=readable_text(value.text), sources=[
                dict(sources[int(ref)])
                for ref in refs if ref.isdigit() and int(ref) < len(sources)])
            if hasattr(value, 'why'):
                result['why'] = readable_text(value.why)
            return result
        row = dict(number=number, name=item.original.trade_name if item else f'Позиция {number}',
            diagnosis=readable_text((rec.facts.get('diagnosis') if rec else None) or diagnosis or ''),
            category=pair.category or 'не установлена',
            evidence=STATUS_LABELS[pair.evidence_status], comment=readable_text(pair.comment),
            established=[], uncertain=[], questions=[], actions=[], sources=sources)
        if rec:
            row['actions'] = [readable_text(action) for action in rec.actions]
            if rec.analysis:
                for field in ('established', 'uncertain', 'questions'):
                    row[field] = [statement(value) for value in getattr(rec.analysis, field)]
        rows.append(row)
    note = ''
    if staff_report:
        note = {'MODEL': 'Разбор подготовлен GigaChat. Рекомендации требуют сверки специалистом.',
                'FALLBACK': 'Использованы резервные рекомендации: подготовить индивидуальный разбор не удалось.',
                'OFFLINE': 'Рекомендации подготовлены по доступным сведениям.'}[staff_report.generation_status]
    classified = sum(p.category is not None for p in app.pairs)
    return dict(schema_version='human-report.1', title='Проверка заявки: ' + name,
        notice='Учебная карточка с реальными названиями. Классификация по клиническим рекомендациям. Решение о выплате принимает специалист. Дозировка не оценивалась.',
        summary=f'Категория назначена для {classified} из {len(app.pairs)} позиций.',
        recommendation_note=note, review_note='Применимость оснований к заявителю требует сверки специалистом.',
        items=rows, costs=[dict(amount=str(c.known_total_amount), currency=c.currency,
            currency_label={'RUB': '₽', 'USD': 'долларов США', 'EUR': 'евро'}.get(c.currency, c.currency),
            unknown_amount_count=c.unknown_amount_count) for c in app.costs_by_currency],
        purchases_without_amount_count=len(app.purchases_without_amount))


def render_human_report(report):
    """Отрисовать только публичное представление; диагностические объекты сюда не поступают."""
    def link(url, label):
        if url.startswith(('https://', 'http://')):
            return '<a href="' + escape(url, quote=True) + '">' + escape(label) + '</a>'
        return escape(label)
    parts = ['<!doctype html><html lang="ru"><meta charset="utf-8"><title>Проверка заявки</title>',
        '<style>body{font:16px/1.5 system-ui;max-width:1100px;margin:32px auto;padding:0 24px;color:#172b45}article{border-top:1px solid #ccd5df;padding:18px 0}blockquote{background:#f1f5fa;padding:12px}a{color:#175a9c}</style>',
        '<h1>' + escape(report['title']) + '</h1>']
    for field in ('notice', 'summary', 'recommendation_note', 'review_note'):
        if report[field]: parts.append('<p>' + escape(report[field]) + '</p>')
    for item in report['items']:
        parts.append('<article><h2>' + escape(item['name']) + '</h2>')
        if item['diagnosis']: parts.append('<p>Диагноз в заявке: ' + escape(item['diagnosis']) + '</p>')
        parts.append('<p>Исходная категория: ' + escape(item['category']) + '. Проверка: ' + escape(item['evidence']) + '.</p>')
        parts.append('<p>' + escape(item['comment']) + '</p>')
        for field, title in [('established', 'Что установлено'), ('uncertain', 'Что не подтверждено'), ('questions', 'Что уточнить и почему')]:
            if not item[field]: continue
            parts.append('<h3>' + title + '</h3><ul>')
            for statement in item[field]:
                parts.append('<li>' + escape(statement['text']) + (' Почему: ' + escape(statement['why']) if 'why' in statement else ''))
                if statement['sources']:
                    parts.append('<p>Основание: ' + '; '.join('<a href="#item-' + str(int(item['number'])) + '-source-' + str(int(s['number'])) + '">' + escape('Выдержка ' + str(int(s['number'])) + ': ' + s['title'] + ' · ' + s['version']) + '</a>' for s in statement['sources']) + '</p>')
                parts.append('</li>')
            parts.append('</ul>')
        if item['actions']:
            parts.append('<h3>Что сделать сотруднику</h3><ol>')
            parts.extend('<li>' + escape(action) + '</li>' for action in item['actions'])
            parts.append('</ol>')
        groups = {}
        for source in item['sources']:
            groups.setdefault((source['title'], source['version'], source['url'], source['copy_url']), []).append(source)
        for (title, version, url, copy_url), quotes in groups.items():
            parts.append('<h3>' + link(url, title) + ' · ' + escape(version) + '</h3>')
            parts.append('<p>Текст сверен с сохранённым источником. Применимость к заявителю требует отдельной сверки.</p>')
            grouped_quotes = {}
            for source in quotes:
                grouped_quotes.setdefault(source['quote'], []).append(source)
            for quote, entries in grouped_quotes.items():
                for entry in entries:
                    parts.append('<span id="item-' + str(int(item['number'])) + '-source-' + str(int(entry['number'])) + '"></span>')
                parts.append('<p>Выдержка ' + ', '.join(str(e['number']) for e in entries) + '</p>')
                parts.append('<blockquote>' + escape(quote) + '</blockquote>')
                if entries[0]['location']: parts.append('<p>Место в документе: ' + escape(entries[0]['location']) + '</p>')
            parts.append('<p>' + link(url, 'Оригинал источника') + '</p>')
            if copy_url: parts.append('<p>Текст получен из ' + link(copy_url, 'копии документа') + '.</p>')
        parts.append('</article>')
    parts.append('<h2>Расходы</h2>')
    for cost in report['costs']:
        parts.append('<p>' + escape(cost['amount']) + ' ' + escape(cost['currency_label']) + '; покупок с неизвестной суммой: ' + str(cost['unknown_amount_count']) + '.</p>')
    if report['purchases_without_amount_count']:
        parts.append('<p>Неизвестная сумма: ' + str(report['purchases_without_amount_count']) + ' покупок.</p>')
    return '\n'.join([*parts, '</html>'])
