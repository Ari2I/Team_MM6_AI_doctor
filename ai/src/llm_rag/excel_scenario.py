"""Автономный сценарий аналитика: исходная Excel-карточка → отчёт."""

from dataclasses import dataclass
from time import perf_counter

from llm_rag.diagnostics import safe_diagnostic
from contextlib import ExitStack
from decimal import Decimal, InvalidOperation
from io import BytesIO
import re
from pathlib import Path
from zipfile import ZipFile

from openpyxl import load_workbook
from pydantic import ValidationError

from llm_rag.contracts import AnalysisRequest
from llm_rag.excel_amount import check_numeric_amounts, numeric_cells
from llm_rag.ml2.classifier import gigachat_completer
from llm_rag.providers.base import PROVIDER_ERROR_MESSAGES


HEADERS = ['ID позиции', 'Препарат', 'Дозировка', 'Количество', 'Сумма', 'Валюта']
CARD_FIELDS = {'ID заявки', 'ID диагноза', 'Диагноз', 'Код МКБ-10', 'Возраст',
               'Учебная заявка', 'Редакция КР', 'Возрастная группа'}
MAX_LEGACY_QUANTITY = 1_000_000


@dataclass
class ExcelCard:
    request: AnalysisRequest
    diagnosis_id: str


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'Не заполнено текстовое поле: {label}.')
    return value.strip()


def _number(value, label, *, integer_bounds=None):
    if isinstance(value, bool) or value is None:
        raise ValueError(f'Не заполнено число: {label}.')
    text = str(value).replace(' ', '').replace('\xa0', '').replace(',', '.')
    if integer_bounds is None:
        # Сумму проверяет контракт покупки до преобразования в Decimal.
        return text
    try:
        number = Decimal(text)
    except InvalidOperation:
        raise ValueError(f'Некорректное число: {label}.') from None
    if not number.is_finite() or number < 0:
        raise ValueError(f'Недопустимое число: {label}.')
    minimum, maximum = integer_bounds
    if not minimum <= number <= maximum or number != number.to_integral_value():
        raise ValueError(f'{label}: нужно целое число от {minimum} до {maximum}.')
    return int(number)


def read_card(path: Path, *, content: bytes | None = None) -> ExcelCard:
    """Читать только разрешённые поля; формулы не выполнять и не подменять кэшем."""
    if content is None:
        if path.stat().st_size > 10_000_000:
            raise ValueError('Excel превышает 10 МБ.')
        content = path.read_bytes()
    if len(content) > 10_000_000:
        raise ValueError('Excel превышает 10 МБ.')
    with ZipFile(BytesIO(content)) as archive:
        if sum(i.file_size for i in archive.infolist()) > 100_000_000:
            raise ValueError('Распакованный Excel превышает 100 МБ.')
    resources = ExitStack()
    try:
        stream = resources.enter_context(BytesIO(content))
        book = load_workbook(stream, read_only=True, data_only=False, keep_links=False)
        resources.callback(book.close)
        if not {'Заявка', 'Покупки'} <= set(book.sheetnames):
            raise ValueError('Нужны листы «Заявка» и «Покупки» по шаблону.')
        def values(sheet, columns, limit):
            # Размеры в XML бывают устаревшими: проверяем реальные строки,
            # иначе read_only молча отбросит покупки за заявленной границей.
            sheet.reset_dimensions()
            for number, row in enumerate(sheet.iter_rows(max_col=columns), 1):
                if number > limit:
                    raise ValueError(f'Слишком много строк на листе «{sheet.title}».')
                if any(cell.data_type == 'f' for cell in row):
                    raise ValueError(f'Используйте значения вместо формул на листе «{sheet.title}».')
                yield [c.value for c in row]
        metadata = list(values(book['Заявка'], 2, 30))
        if not metadata or metadata[0] != ['Поле', 'Значение']:
            raise ValueError('На листе «Заявка» нужны заголовки «Поле», «Значение».')
        fields = {}
        for row_number, (key, value) in enumerate(metadata[1:], 2):
            if key is None and value is None:
                continue
            if key not in CARD_FIELDS or key in fields:
                raise ValueError('Неизвестное или повторное поле карточки. Используйте шаблон без персональных данных.')
            if key == 'Возраст' and isinstance(value, float):
                # Положение поля определяется названием, порядок строк произвольный.
                age_values = {row: raw for row, _, raw in numeric_cells(book['Заявка'], 30, (2,))}
                value = age_values.get(row_number, value)
            fields[key] = value
        raw_age = fields.get('Возраст')
        age = None if raw_age is None or raw_age == '' else _number(raw_age, 'Возраст', integer_bounds=(0, 130))
        code = _text(fields.get('Код МКБ-10'), 'Код МКБ-10').upper()
        if not re.fullmatch(r'[A-Z][0-9]{2}(?:\.[0-9]{1,4})?', code):
            raise ValueError('Некорректный код МКБ-10.')
        rows = iter(values(book['Покупки'], 6, 501))
        if next(rows, None) != HEADERS:
            raise ValueError('Заголовки листа «Покупки» не совпадают с шаблоном.')
        raw_quantities = {}
        check_numeric_amounts(book['Покупки'], 501, legacy_quantities=raw_quantities)
        items = []
        for row_number, row in enumerate(rows, 2):
            if all(v is None for v in row):
                continue
            item_id, trade, dosage, quantity, amount, currency = row
            if isinstance(quantity, float):
                quantity = raw_quantities.get(row_number, quantity)
            items.append(dict(item_id=_text(item_id, f'ID позиции, строка {row_number}'),
                original=dict(trade_name=_text(trade, f'Препарат, строка {row_number}'),
                    dosage=_text(dosage, f'Дозировка, строка {row_number}'),
                    quantity=_number(quantity, f'Количество, строка {row_number}', integer_bounds=(1, MAX_LEGACY_QUANTITY)),
                    amount=_number(amount, f'Сумма, строка {row_number}'), currency=_text(currency, 'Валюта')),
                inn=None, category=None, classification_reason=None,
                dosage_check=dict(status='NOT_CHECKED', reason=None, missing_data=[]),
                source_refs=[], upstream_status='PARTIAL', missing_data=[]))
        context = dict(icd10=code)
        if age is not None:
            context.update(age=str(age), age_group='Взрослые' if age >= 18 else 'Дети')
        group = fields.get('Возрастная группа')
        if group not in {None, '', 'adult', 'child'}:
            raise ValueError('Возрастная группа должна быть adult или child.')
        if group:
            if age is not None and group != ('adult' if age >= 18 else 'child'):
                raise ValueError('Возрастная группа противоречит точному возрасту.')
            context['age_group'] = {'adult': 'Взрослые', 'child': 'Дети'}[group]
        if fields.get('Редакция КР'):
            context['recommendation_edition'] = _text(fields['Редакция КР'], 'Редакция КР')
        try:
            request = AnalysisRequest(schema_version='1.0', request_id=_text(fields.get('ID заявки'), 'ID заявки'),
                diagnosis=_text(fields.get('Диагноз'), 'Диагноз'), items=items, context=context, )
        except ValidationError:
            raise ValueError('Проверьте уникальность ID позиций, количество (>0), сумму (>=0) и валюту (RUB/USD/...).') from None
        return ExcelCard(request, _text(fields.get('ID диагноза'), 'ID диагноза'))
    finally:
        resources.close()


def collect_files(folder: Path) -> list[Path]:
    """Папка не сканируется рекурсивно; временные файлы Excel пропускаются."""
    return sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == '.xlsx'
                   and not p.name.startswith('~$')), key=lambda p: p.name)


class CallCounter:
    """Раздельно учитывать вызовы классификации и оформления без секретов."""

    name = 'gigachat-counted'

    def __init__(self, provider, *, classification=False):
        self.provider = provider
        self.classification = classification
        self.stats = dict(calls=0, responses=0, tokens=0, errors=[], diagnostics=[])
        self.complete = gigachat_completer(provider) if classification else None

    def __call__(self, *args):
        started = perf_counter()
        before_calls=getattr(self.provider,'api_calls',None)
        if before_calls is None: self.stats['calls'] += 1
        before = self.provider.usage_totals['total_tokens']
        try:
            response = self.complete(*args) if self.classification else self.provider.generate(*args)
            self.stats['responses'] += 1
            return response
        except Exception as error:
            diagnostic = getattr(self.provider, 'last_diagnostic', None) or safe_diagnostic(
                error, stage='provider_request', elapsed_ms=(perf_counter() - started) * 1000)
            self.stats['diagnostics'].append(diagnostic)
            code = diagnostic['code']
            self.stats['errors'].append(PROVIDER_ERROR_MESSAGES.get(code, 'Ошибка API; сырой текст исключения скрыт.'))
            raise
        finally:
            if before_calls is not None:
                self.stats['calls']+=self.provider.api_calls-before_calls
            tokens = (getattr(self.provider, 'last_total_tokens', None) if self.classification else
                      self.provider.usage_totals['total_tokens'] - before)
            if isinstance(tokens, int):
                self.stats['tokens'] += tokens

    def generate(self, payload):
        return self(payload)


def write_report(folder, name, app, usage, items, *, staff_report=None, human_report=None, diagnosis=None):
    """Сохранить HTML из того же публичного представления, которое получает интерфейс."""
    from llm_rag.human_report import build_human_report, render_human_report
    folder.mkdir(parents=True, exist_ok=True)
    human = human_report if human_report is not None else build_human_report(
        name, app, items, staff_report=staff_report, diagnosis=diagnosis)
    output = folder / 'report.html'
    output.write_text(render_human_report(human), encoding='utf-8')
    return output
