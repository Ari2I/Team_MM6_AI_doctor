import os
import subprocess
import sys

from tests.helpers import ROOT
from tests.test_gigachat import sdk_wire, completion, make_provider


def load_checker():
    import importlib.util
    spec = importlib.util.spec_from_file_location('explanation_checker', ROOT / 'check_explanations.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_readable_offline_check_from_another_directory(tmp_path):
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    completed = subprocess.run([sys.executable, str(ROOT / 'check_explanations.py'), '--offline'],
        cwd=tmp_path, env=env, capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert 'БЕЗ API' in completed.stdout
    assert 'Проверка пройдена' in completed.stdout
    assert 'Вызовы провайдера: 0' in completed.stdout
    assert '200.00 RUB' in completed.stdout
    assert '???' not in completed.stdout


def test_api_response_and_usage_are_checked(sdk_wire, tmp_path, capsys):
    import json
    import httpx
    def handler(request):
        if request.url.path == '/api/v2/oauth':
            return httpx.Response(200, json={'access_token': 'test-access', 'expires_at': 1_900_000_000_000})
        context = json.loads(json.loads(request.content)['messages'][1]['content'])
        return httpx.Response(200, json=completion({'item_id': context['item']['item_id'], 'claims': context['evidence']}))
    sdk_wire.handler = handler
    checker = load_checker()
    with make_provider() as provider:
        observed = checker.ObservedProvider(provider)
        assert checker.check(observed, output_path=tmp_path / 'result.json')
        assert provider.usage_totals['total_tokens'] == 280
    output = capsys.readouterr().out
    assert 'получено ответов: 2' in output
    assert 'ОТВЕТ МОДЕЛИ ПРИНЯТ' in output


def test_api_failure_is_not_successful_check(sdk_wire, tmp_path, capsys):
    import httpx
    sdk_wire.handler = lambda request: httpx.Response(403, text='private-error')
    checker = load_checker()
    with make_provider() as provider:
        assert not checker.check(checker.ObservedProvider(provider), output_path=tmp_path / 'result.json')
    output = capsys.readouterr().out
    assert 'Проверка НЕ пройдена' in output
    assert 'РЕЗЕРВНЫЙ ШАБЛОН' in output
    assert 'private-error' not in output
