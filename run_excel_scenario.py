"""Прочитать Excel-карточки из excel_input и сохранить отчёты в excel_results."""

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'src'))

from dotenv import dotenv_values
import httpx
from llm_rag.excel_scenario import read_card, collect_files, CapturingSources, CallCounter, process_card, write_report
from llm_rag.ml2.grls import download_latest, build_registry_snapshot
from llm_rag.ml2.provider import ML2GigaChatProvider
from llm_rag.ml2.registry import RegistrySnapshot
from llm_rag.providers.gigachat import GigaChatProvider
from llm_rag.staff_report import recommend_for_staff
from llm_rag.explanation_contracts import prepare_explanation_inputs


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path, default=ROOT / 'excel_input')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'excel_results')
    parser.add_argument('--registry', type=Path, default=ROOT / '.local/official-inputs/grls/registry.json')
    tls = parser.add_mutually_exclusive_group()
    tls.add_argument('--insecure', dest='verify_ssl_certs', action='store_false',
                     help='Локальный режим без проверки сертификата GigaChat (по умолчанию по выбору пользователя)')
    tls.add_argument('--verify-ssl', dest='verify_ssl_certs', action='store_true',
                     help='Включить проверку сертификата GigaChat')
    parser.set_defaults(verify_ssl_certs=False)
    args = parser.parse_args(argv)
    args.input_dir.mkdir(parents=True, exist_ok=True)
    files = collect_files(args.input_dir)
    if not files:
        print(f'Нет файлов .xlsx. Положите карточку по шаблону в: {args.input_dir}')
        return 2
    print(f'Файлов: {len(files)}. Реальный сценарий ML-2 → LLM/RAG. Будут расходоваться токены GigaChat.', flush=True)
    print('Сертификат GigaChat: ' + ('проверяется.' if args.verify_ssl_certs else
          'проверка отключена в локальном сценарии по выбору пользователя.'), flush=True)
    # Ошибочные карточки не запускают загрузку справочников или модель.
    valid, rejected = [], []
    for path in files:
        try:
            if path.stat().st_size > 10_000_000:
                raise ValueError('Excel превышает 10 МБ.')
            content = path.read_bytes()
            valid.append((path, read_card(path, content=content), sha256(content).hexdigest()))
        except Exception as error:
            message = str(error) if isinstance(error, ValueError) else 'Не удалось прочитать Excel. Проверьте формат и доступ к файлу.'
            print(f'{path.name}: ОШИБКА ВХОДА. {message}', flush=True)
            rejected.append(dict(file=path.name, error=message))
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
    output = args.output_dir / stamp
    output.mkdir(parents=True, exist_ok=False)
    (output / 'input_errors.json').write_text(json.dumps(rejected, ensure_ascii=False, indent=2), encoding='utf-8')
    if not valid:
        return 2
    try:
        if not args.registry.exists():
            print('Первый запуск: загрузка публичного ГРЛС, это может занять несколько минут.', flush=True)
            archive, as_of, _ = download_latest(args.registry.parent)
            build_registry_snapshot(archive, as_of, args.registry)
        registry = RegistrySnapshot.load(args.registry)
        print(f'ГРЛС: {registry.version}; записей: {len(registry.registrations)}.', flush=True)
        settings = {**dotenv_values(ROOT / '.env', encoding='utf-8-sig', interpolate=False), **os.environ}
        ca = settings.get('GIGACHAT_CA_BUNDLE_FILE') or settings.get('GIGACHAT_CA_BUNDLE')
        if ca and not Path(ca).is_absolute():
            ca = str(ROOT / ca)
        kwargs = dict(credentials=settings.get('GIGACHAT_CREDENTIALS'),
            access_token=settings.get('GIGACHAT_ACCESS_TOKEN'), enabled=True,
            model=settings.get('GIGACHAT_MODEL') or 'GigaChat-2',
            scope=settings.get('GIGACHAT_SCOPE') or 'GIGACHAT_API_PERS',
            ca_bundle=ca, verify_ssl_certs=args.verify_ssl_certs, timeout=60)
        technical_errors = False
        with ExitStack() as stack:
            ml2_provider = stack.enter_context(ML2GigaChatProvider(**kwargs))
            render_provider = stack.enter_context(GigaChatProvider(**kwargs))
            for number, (path, card, input_digest) in enumerate(valid, 1):
                target = output / f'{number:03d}'
                try:
                    print(f'\n[{number}/{len(valid)}] {path.name}: поиск источников и классификация…', flush=True)
                    sources = CapturingSources(ROOT / '.local/official-inputs/clinicalrecs')
                    classifier = CallCounter(ml2_provider, classification=True)
                    renderer = CallCounter(render_provider)
                    app, batch, snapshots = process_card(card, registry, sources, classifier, renderer)
                    report_renderer = CallCounter(render_provider)
                    report_inputs = prepare_explanation_inputs(batch.request, diagnosis_id=card.diagnosis_id, ml2_details=batch.details())
                    staff = recommend_for_staff(report_inputs, app.pairs, provider=report_renderer)
                    usage = dict(classification=classifier.stats, explanation=renderer.stats, staff_report=report_renderer.stats)
                    report = write_report(target, path.name, app, usage, batch.request.items, staff_report=staff)
                    record = dict(file=path.name, input_sha256=input_digest,
                        registry_version=registry.version, created_at=datetime.now(timezone.utc).isoformat(),
                        usage=usage, ml2_details=batch.details(), evidence_snapshots=snapshots,
                        application=app.model_dump(mode='json'), staff_report=staff.model_dump(mode='json'))
                    (target / 'result.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
                    for item, pair in zip(batch.request.items, app.pairs):
                        print(f'  {item.original.trade_name}: МНН={item.inn or "не установлен"}; '
                              f'категория={pair.category or "не установлена"}; ML-2={pair.ml2_status}; основание={pair.evidence_status}')
                    bad = staff.generation_status == 'FALLBACK' or any(p.evidence_status == 'PROCESSING_ERROR' or p.rendering_reasons for p in app.pairs)
                    technical_errors |= bad
                    print('  ' + ('ТЕХНИЧЕСКИЕ ОШИБКИ. ' if bad else 'Обработка завершена; требуется сверка специалистом. '))
                    print(f'  Категория назначена для {sum(p.category is not None for p in app.pairs)} из {len(app.pairs)} позиций.')
                    print(f'  GigaChat: классификация {classifier.stats["calls"]} вызовов, {classifier.stats["tokens"]} токенов; '
                          f'оформление {renderer.stats["calls"]} вызовов, {renderer.stats["tokens"]} токенов.')
                    print(f'  Рекомендация сотруднику: {staff.generation_status}; {report_renderer.stats["tokens"]} токенов.')
                    print(f'  Отчёт: {report}', flush=True)
                except Exception:
                    technical_errors = True
                    target.mkdir(parents=True, exist_ok=True)
                    (target / 'error.txt').write_text('Техническая ошибка обработки. Проверьте доступ к публичным источникам и настройки API.', encoding='utf-8')
                    print(f'  Техническая ошибка. Остальные файлы будут обработаны. См. {target}', flush=True)
        print(f'\nРезультаты запуска: {output}')
        return 1 if technical_errors else 2 if rejected else 0
    except (OSError, ValueError, httpx.HTTPError):
        message = 'Сценарий не завершён: проверьте доступ к ГРЛС, файл реестра, ключ/токен, scope и сертификат GigaChat.'
        (output / 'error.txt').write_text(message, encoding='utf-8')
        print(message, flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
