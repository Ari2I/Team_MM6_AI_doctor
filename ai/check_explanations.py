"""Ручная проверка нового пути пояснений на синтетическом примере."""

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'src'))

from dotenv import dotenv_values
from llm_rag.application import read_json
from llm_rag.corpus import Corpus
from llm_rag.evidence_snapshot import EvidenceSnapshot
from llm_rag.explanation import explain_pairs
from llm_rag.explanation_contracts import ExplanationInput, PurchaseAmount
from llm_rag.explanation_summary import summarize_explanations
from llm_rag.providers.base import PROVIDER_ERROR_MESSAGES
from llm_rag.providers.gigachat import GigaChatProvider


class ObservedProvider:
    """Считать обращения и обезличенные ошибки, не подменяя ответы API."""

    name = 'observed-gigachat'

    def __init__(self, provider):
        self.provider = provider
        self.calls = 0
        self.responses = 0
        self.errors = []

    def generate(self, payload):
        self.calls += 1
        try:
            result = self.provider.generate(payload)
            self.responses += 1
            return result
        except Exception as error:
            code = str(error)
            self.errors.append(PROVIDER_ERROR_MESSAGES.get(code, 'Техническая ошибка обращения к модели.'))
            raise


def check(provider=None, *, output_path=None) -> bool:
    """Сопоставить результат с эталоном; резервный шаблон не считать успехом API."""
    data = read_json(ROOT / 'examples/ml2_explanation.json')
    pairs = explain_pairs([ExplanationInput.model_validate(x) for x in data['inputs']],
        EvidenceSnapshot.model_validate(data['snapshot']),
        approved_corpus=Corpus.model_validate(data['approved_corpus']), provider=provider)
    result = summarize_explanations(pairs, purchases=[PurchaseAmount.model_validate(x) for x in data['purchases']])
    expected = data['expected_result']
    passed = len(pairs) == len(expected['pairs'])
    for pair, reference in zip(pairs, expected['pairs']):
        actual = pair.model_dump(mode='json')
        # Модель может переставить разрешённые цитаты; факты и ограничения неизменны.
        ignored = {'comment', 'citations', 'rendering_reasons'}
        same = ({k: v for k, v in actual.items() if k not in ignored}
                == {k: v for k, v in reference.items() if k not in ignored}
                and sorted(json.dumps(c, sort_keys=True) for c in actual['citations'])
                == sorted(json.dumps(c, sort_keys=True) for c in reference['citations']))
        passed = passed and same and not pair.rendering_reasons
        rendering = ('БЕЗ API' if provider is None else 'РЕЗЕРВНЫЙ ШАБЛОН' if pair.rendering_reasons
                     else 'ОТВЕТ МОДЕЛИ ПРИНЯТ')
        print(f'\n{pair.item_id}: категория {pair.category}; {"СОВПАЛО С ЭТАЛОНОМ" if same else "РАСХОЖДЕНИЕ"}')
        print(f'Оформление: {rendering}')
        print(pair.comment)
    passed = passed and result.model_dump(mode='json')['costs_by_currency'] == expected['costs_by_currency']
    for cost in result.costs_by_currency:
        print(f'\nРасходы: {cost.known_total_amount} {cost.currency}')
    if provider is not None:
        print(f'Вызовы провайдера: {provider.calls}; получено ответов: {provider.responses}')
        print('Токены из ответов GigaChat:', json.dumps(provider.provider.usage_totals, ensure_ascii=False))
        for error in provider.errors:
            print('Ошибка API:', error)
        passed = passed and provider.responses == len(pairs) and provider.calls == len(pairs)
    else:
        print('Вызовы провайдера: 0. Работа модели этим запуском не проверялась.')
    output = output_path or ROOT / '.local/results/explanation-check.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(result.model_dump_json(indent=2) + '\n', encoding='utf-8')
    print('Проверка пройдена.' if passed else 'Проверка НЕ пройдена: см. расхождения или ошибки выше.')
    print('JSON результата:', output)
    return passed


def main(argv=None) -> int:
    """Выбрать явный режим, прочитать локальные настройки и закрыть SDK."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--gigachat', action='store_true', help='Реальные запросы GigaChat на синтетическом примере')
    mode.add_argument('--offline', action='store_true', help='Только логика библиотеки, без API')
    parser.add_argument('--insecure', action='store_true', help='Отключить проверку сертификата только для этого запуска')
    args = parser.parse_args(argv)
    if args.insecure and args.offline:
        parser.error('--insecure применяется только с --gigachat')
    if args.offline:
        print('Режим: БЕЗ API')
        return 0 if check() else 1
    print('Режим: РЕАЛЬНЫЙ GigaChat. Две синтетические позиции; расходуются токены.', flush=True)
    if args.insecure:
        print('Проверка сертификата отключена для этого запуска.', flush=True)
    settings = {**dotenv_values(ROOT / '.env', encoding='utf-8-sig', interpolate=False), **os.environ}
    ca = settings.get('GIGACHAT_CA_BUNDLE_FILE')
    if ca and not Path(ca).is_absolute():
        ca = str(ROOT / ca)
    try:
        with GigaChatProvider(credentials=settings.get('GIGACHAT_CREDENTIALS'),
                access_token=settings.get('GIGACHAT_ACCESS_TOKEN'), enabled=True,
                model=settings.get('GIGACHAT_MODEL') or 'GigaChat-2',
                scope=settings.get('GIGACHAT_SCOPE') or 'GIGACHAT_API_PERS',
                ca_bundle=ca, timeout=60.0, verify_ssl_certs=not args.insecure) as provider:
            return 0 if check(ObservedProvider(provider)) else 1
    except (ValueError, OSError):
        print('Не удалось настроить GigaChat. Проверьте ключ/токен, scope, модель и сертификат в .env.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
