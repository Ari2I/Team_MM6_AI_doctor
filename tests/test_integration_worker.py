"""Обмен целой заявкой: ошибки, строгая идентичность, завершение и отсутствие утечек."""

import json
import os
import subprocess
import sys
import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.config import AISettings
from app.services import integration_client, integration_worker
from app.services.integration_client import IntegrationWorkerClient
from app.services.integration_mapping import prepare_integration_request
from app.services.integration_protocol import IntegrationReply, IntegrationTask
from tests.ai_report_data import integration_result
from tests.test_integration_mapping import make_application

pytestmark = pytest.mark.usefixtures("no_network")


@pytest.fixture
def library_path(monkeypatch: pytest.MonkeyPatch) -> None:
    path = Path("ai/src").resolve()
    if not path.is_dir():
        pytest.skip("AI library is absent")
    monkeypatch.syspath_prepend(str(path))
    monkeypatch.setitem(sys.modules, "gigachat", None)


def prepared():
    return prepare_integration_request(make_application(), currency="RUB")


def worker_client() -> IntegrationWorkerClient:
    return IntegrationWorkerClient(AISettings(mode="network", application_timeout_seconds=90))


def task(mode: str = "network") -> IntegrationTask:
    value = prepared()
    return IntegrationTask(
        request_id=UUID(value.payload["request_id"]),
        mode=mode,
        timeout_seconds=5.0,
        payload=value.payload,
    )


def fake_process(monkeypatch: pytest.MonkeyPatch, reply: IntegrationReply) -> list[dict]:
    calls = []

    def run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        return SimpleNamespace(stdout=reply.model_dump_json())

    monkeypatch.setattr(integration_client.subprocess, "run", run)
    return calls


@pytest.mark.parametrize("mode", ["template", "offline"])
def test_local_client_and_worker_skip_before_imports(
    mode: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "llm_rag", None)
    monkeypatch.setitem(sys.modules, "gigachat", None)

    def forbidden(*args, **kwargs):
        pytest.fail("Локальный режим запустил процесс или фасад.")

    monkeypatch.setattr(integration_client.subprocess, "run", forbidden)
    monkeypatch.setattr(integration_worker, "run_request", forbidden)
    settings = AISettings(mode=mode, registry_path="/unused", live_sources=True)
    assert IntegrationWorkerClient(settings).analyze(prepared()).outcome == "skipped"
    assert integration_worker.execute(task(mode)).outcome == "skipped"


@pytest.mark.parametrize("mode", ["complete", "partial", "fallback"])
def test_client_accepts_valid_result_and_keeps_input(
    mode: str,
    library_path: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = integration_result(make_application(), mode)
    reply = IntegrationReply(request_id=UUID(int=100), outcome="result", result=result)
    calls = fake_process(monkeypatch, reply)
    value = prepared()
    before = deepcopy(value.payload)
    response = worker_client().analyze(value)
    assert response.outcome == "result" and response.result == result
    assert value.payload == before
    assert len(calls) == 1
    call = calls[0]
    assert call["command"] == [sys.executable, "-m", "app.services.integration_worker"]
    sent = IntegrationTask.model_validate_json(call["input"])
    assert sent.request_id == UUID(int=100) and sent.mode == "network"
    assert 0 < sent.timeout_seconds <= 90
    assert call["check"] is True and call["capture_output"] is True
    assert "env" not in call  # Секреты не копируются в JSON или тестовый вывод.


@pytest.mark.parametrize(
    "damage",
    [
        "json",
        "extra_stdout",
        "unknown_field",
        "version",
        "missing_version",
        "foreign_id",
        "skipped",
        "unknown_error",
        "both_outcomes",
        "missing_result",
        "wrong_result_request",
        "changed_age_group",
        "bad_result",
    ],
)
def test_client_rejects_invalid_protocol(
    damage: str,
    library_path: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    result = integration_result(make_application())
    data = IntegrationReply(request_id=UUID(int=100), outcome="result", result=result).model_dump(
        mode="json"
    )
    if damage == "unknown_field":
        data["private"] = "PRIVATE-WORKER-MARKER"
    elif damage == "version":
        data["schema_version"] = "integration-worker-response.2"
    elif damage == "missing_version":
        data.pop("schema_version")
    elif damage == "foreign_id":
        data["request_id"] = str(uuid4())
    elif damage == "skipped":
        data.update(outcome="skipped", result=None)
    elif damage == "unknown_error":
        data.update(outcome="error", result=None, error="PRIVATE-WORKER-MARKER")
    elif damage == "both_outcomes":
        data["error"] = "AI_UNAVAILABLE"
    elif damage == "missing_result":
        data["result"] = None
    elif damage == "wrong_result_request":
        # Результат остаётся валидным сам по себе, но относится к другой заявке.
        other = make_application()
        other.id = uuid4()
        data["result"] = integration_result(other)
    elif damage == "changed_age_group":
        data["result"]["request"]["diagnoses"][0]["conditions"]["age_group"] = "child"
    elif damage == "bad_result":
        data["result"]["human_report"]["title"] = "PRIVATE-WORKER-MARKER"
    output = json.dumps(data)
    if damage == "json":
        output = "PRIVATE-WORKER-MARKER"
    elif damage == "extra_stdout":
        output = "PRIVATE-WORKER-MARKER\n" + output
    monkeypatch.setattr(
        integration_client.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=output)
    )
    reply = worker_client().analyze(prepared())
    assert reply.error == "AI_INVALID_RESPONSE" and reply.result is None
    assert "PRIVATE-WORKER-MARKER" not in reply.model_dump_json()
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "code", ["AI_INPUT_INCOMPLETE", "AI_UNAVAILABLE", "AI_TIMEOUT", "AI_INVALID_RESPONSE"]
)
def test_known_worker_error_is_preserved(
    code: str,
    library_path: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_process(
        monkeypatch,
        IntegrationReply.model_validate_json(
            json.dumps(
                dict(
                    request_id=str(UUID(int=100)),
                    outcome="error",
                    error=code,
                )
            )
        ),
    )
    reply = worker_client().analyze(prepared())
    assert reply.error == code and reply.result is None


@pytest.mark.parametrize(
    "operation,code",
    [
        (subprocess.TimeoutExpired("worker", 1, output="PRIVATE-WORKER-MARKER"), "AI_TIMEOUT"),
        (
            subprocess.CalledProcessError(1, "worker", output="PRIVATE-WORKER-MARKER"),
            "AI_UNAVAILABLE",
        ),
        (OSError("PRIVATE-WORKER-MARKER"), "AI_UNAVAILABLE"),
    ],
)
def test_subprocess_errors_do_not_publish_output(
    operation: Exception,
    code: str,
    library_path: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    def run(*args, **kwargs):
        raise operation

    monkeypatch.setattr(integration_client.subprocess, "run", run)
    reply = worker_client().analyze(prepared())
    assert reply.error == code and reply.result is None
    assert "PRIVATE-WORKER-MARKER" not in reply.model_dump_json()
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


@pytest.mark.parametrize("group", ["adult", "child"])
def test_age_group_survives_client_worker_roundtrip(
    group: str, library_path: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    application = make_application()
    application.age_group = group
    expected = integration_result(application)
    calls = []

    def facade(payload, budget):
        calls.append(payload)
        assert all(
            diagnosis["conditions"]["age_group"] == group
            and diagnosis["conditions"]["age_years"] is None
            for diagnosis in payload["diagnoses"]
        )
        return expected

    def run(command, **kwargs):
        sent = IntegrationTask.model_validate_json(kwargs["input"])
        response = integration_worker.execute(sent)
        return SimpleNamespace(stdout=response.model_dump_json())

    monkeypatch.setattr(integration_worker, "run_request", facade)
    monkeypatch.setattr(integration_client.subprocess, "run", run)
    reply = worker_client().analyze(prepare_integration_request(application, currency="RUB"))
    assert reply.outcome == "result" and reply.result == expected
    assert len(calls) == 1


@pytest.mark.parametrize("group", ["adult", "child"])
def test_client_rejects_result_that_dropped_age_group(
    group: str, library_path: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    application = make_application()
    result_without_group = integration_result(application)
    application.age_group = group
    fake_process(
        monkeypatch,
        IntegrationReply(request_id=application.id, outcome="result", result=result_without_group),
    )
    reply = worker_client().analyze(prepare_integration_request(application, currency="RUB"))
    assert reply.error == "AI_INVALID_RESPONSE" and reply.result is None


def test_no_budget_does_not_start_process(
    library_path: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    ticks = iter([0.0, 2.0])
    monkeypatch.setattr(integration_client, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(
        integration_client.subprocess, "run", lambda *a, **k: pytest.fail("Нет бюджета")
    )
    reply = IntegrationWorkerClient(
        AISettings(mode="network", application_timeout_seconds=1)
    ).analyze(prepared())
    assert reply.error == "AI_TIMEOUT"


@pytest.mark.parametrize(
    "error,code",
    [
        (TimeoutError("PRIVATE-WORKER-MARKER"), "AI_TIMEOUT"),
        (RuntimeError("PRIVATE-WORKER-MARKER"), "AI_UNAVAILABLE"),
        (ValueError("PRIVATE-WORKER-MARKER"), "AI_INVALID_RESPONSE"),
    ],
)
def test_worker_maps_facade_errors(
    error: Exception,
    code: str,
    library_path: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run(*args):
        raise error

    monkeypatch.setattr(integration_worker, "run_request", run)
    reply = integration_worker.execute(task())
    assert reply.error == code and reply.result is None
    assert "PRIVATE-WORKER-MARKER" not in reply.model_dump_json()


def test_worker_rejects_invalid_input_before_facade(
    library_path: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(integration_worker, "run_request", lambda *a: pytest.fail("Вызван фасад"))
    value = task()
    value.payload["request_id"] = str(uuid4())
    assert integration_worker.execute(value).error == "AI_INPUT_INCOMPLETE"


def test_facade_call_has_tls_and_explicit_budget(
    library_path: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    expected = integration_result(make_application(), "partial")

    def analyze(request, *, verify_ssl_certs, options):
        calls.append((verify_ssl_certs, options.deadline_seconds))
        return SimpleNamespace(model_dump=lambda **kw: expected)

    monkeypatch.setitem(
        sys.modules,
        "llm_rag.integration",
        SimpleNamespace(
            analyze_request=analyze,
            IntegrationOptions=lambda **kw: SimpleNamespace(**kw),
        ),
    )
    response = integration_worker.execute(task())
    assert response.result == expected and response.error is None
    assert calls == [(True, 5.0)]


def test_real_worker_rejects_malformed_stdin_without_leak() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "app.services.integration_worker"],
        input="PRIVATE-WORKER-MARKER",
        capture_output=True,
        text=True,
        timeout=10,
    )
    response = IntegrationReply.model_validate_json(result.stdout)
    assert result.returncode == 0 and result.stderr == ""
    assert response.error == "AI_INPUT_INCOMPLETE" and response.request_id is None
    assert "PRIVATE-WORKER-MARKER" not in result.stdout


@pytest.mark.parametrize("mode", ["template", "offline"])
def test_real_local_worker_without_ai_or_sdk(mode: str) -> None:
    script = """
import sys,runpy
sys.modules['llm_rag']=None
sys.modules['gigachat']=None
runpy.run_module('app.services.integration_worker',run_name='__main__')
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        input=task(mode).model_dump_json(),
        capture_output=True,
        text=True,
        timeout=10,
    )
    response = IntegrationReply.model_validate_json(result.stdout)
    assert result.returncode == 0 and result.stderr == ""
    assert response.outcome == "skipped"


def test_real_worker_requires_version_on_wire() -> None:
    payload = task().model_dump(mode="json")
    payload.pop("schema_version")
    result = subprocess.run(
        [sys.executable, "-m", "app.services.integration_worker"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert IntegrationReply.model_validate_json(result.stdout).error == "AI_INPUT_INCOMPLETE"
    assert result.stderr == ""


def test_reply_after_deadline_is_not_accepted(
    library_path: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reply = IntegrationReply(
        request_id=UUID(int=100), outcome="result", result=integration_result(make_application())
    )
    fake_process(monkeypatch, reply)
    ticks = iter([0.0, 0.1, 91.0])
    monkeypatch.setattr(integration_client, "monotonic", lambda: next(ticks))
    response = worker_client().analyze(prepared())
    assert response.error == "AI_TIMEOUT" and response.result is None


def test_worker_crash_discards_even_valid_stdout(
    library_path: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reply = IntegrationReply(
        request_id=UUID(int=100), outcome="result", result=integration_result(make_application())
    )

    def run(command, **kwargs):
        raise subprocess.CalledProcessError(1, command, output=reply.model_dump_json())

    monkeypatch.setattr(integration_client.subprocess, "run", run)
    response = worker_client().analyze(prepared())
    assert response.error == "AI_UNAVAILABLE" and response.result is None


def test_worker_suppresses_python_and_os_output() -> None:
    script = """
import sys,os,logging
from app.services import integration_worker as worker
from app.services.integration_protocol import IntegrationReply
def execute(task):
 print('PRIVATE-WORKER-MARKER')
 print('PRIVATE-WORKER-MARKER',file=sys.stderr)
 sys.__stdout__.write('PRIVATE-WORKER-MARKER')
 sys.__stderr__.write('PRIVATE-WORKER-MARKER')
 os.write(1,b'PRIVATE-WORKER-MARKER')
 os.write(2,b'PRIVATE-WORKER-MARKER')
 logging.error('PRIVATE-WORKER-MARKER')
 return IntegrationReply(request_id=task.request_id,outcome='error',error='AI_UNAVAILABLE')
worker.execute=execute
worker.main()
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        input=task().model_dump_json(),
        capture_output=True,
        text=True,
        timeout=10,
    )
    response = IntegrationReply.model_validate_json(result.stdout)
    assert result.returncode == 0 and result.stderr == ""
    assert response.error == "AI_UNAVAILABLE"
    assert "PRIVATE-WORKER-MARKER" not in result.stdout


def test_network_worker_without_library_returns_safe_error() -> None:
    script = """
import sys,runpy
sys.modules['llm_rag']=None
sys.modules['gigachat']=None
runpy.run_module('app.services.integration_worker',run_name='__main__')
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        input=task().model_dump_json(),
        capture_output=True,
        text=True,
        timeout=10,
    )
    response = IntegrationReply.model_validate_json(result.stdout)
    assert response.request_id == UUID(int=100)
    assert response.error == "AI_UNAVAILABLE" and response.result is None
    assert result.returncode == 0 and result.stderr == ""


def test_client_resolves_source_library_only_when_called(library_path: None) -> None:
    script = """
import sys,tempfile
from pathlib import Path
sys.modules['gigachat']=None
from pytest import MonkeyPatch
from tests.conftest import no_network
patch=MonkeyPatch()
guard=no_network.__wrapped__(patch,Path(tempfile.mkdtemp()))
next(guard)
try:
 from types import SimpleNamespace
 from uuid import UUID
 from app.services import integration_client
 from app.services.integration_protocol import IntegrationReply
 from app.services.integration_mapping import prepare_integration_request
 from app.config import AISettings
 from tests.test_integration_mapping import make_application
 assert not any(name.startswith('llm_rag.') for name in sys.modules)
 def run(*args,**kwargs):
  from tests.ai_report_data import integration_result
  result=integration_result(make_application())
  return SimpleNamespace(stdout=IntegrationReply(
   request_id=UUID(int=100),outcome='result',result=result).model_dump_json())
 patch.setattr(integration_client.subprocess,'run',run)
 client=integration_client.IntegrationWorkerClient(AISettings(mode='network'))
 result=client.analyze(prepare_integration_request(make_application(),currency='RUB'))
 assert result.outcome=='result' and result.error is None
finally:
 try: next(guard)
 except StopIteration: pass
 patch.undo()
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, "Клиент не нашёл исходники библиотеки или нарушил запрет сети."


def test_real_timeout_kills_and_waits_for_worker(
    library_path: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    original_run = subprocess.run
    pid_file = tmp_path / "pid"
    marker = tmp_path / "not-killed"
    script = f"""
import os,time
from pathlib import Path
Path({str(pid_file)!r}).write_text(str(os.getpid()))
time.sleep(1.0)
Path({str(marker)!r}).touch()
"""

    def run(command, **kwargs):
        kwargs["timeout"] = 0.3
        return original_run([sys.executable, "-c", script], **kwargs)

    monkeypatch.setattr(integration_client.subprocess, "run", run)
    response = worker_client().analyze(prepared())
    assert response.error == "AI_TIMEOUT" and response.result is None
    assert pid_file.exists()
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)
    time.sleep(1.0)
    assert not marker.exists()


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_invalid_timeout_is_rejected(timeout: float) -> None:
    with pytest.raises(ValidationError):
        IntegrationTask(
            request_id=UUID(int=100), mode="network", timeout_seconds=timeout, payload={}
        )
