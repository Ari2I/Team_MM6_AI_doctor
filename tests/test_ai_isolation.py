"""Изоляция запуска API и локальных режимов от AI и сети."""

import ast
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from app.config import AISettings
from app.schemas.analysis import PairCheck
from app.services import ai_worker
from app.services.ai_adapter import AIInput

pytestmark = pytest.mark.usefixtures("no_network")


@pytest.mark.parametrize(
    "operation",
    [
        "socket.socket()",
        "socket.getaddrinfo('localhost', 80)",
        "socket.create_connection(('localhost', 80))",
        "exec('import socket; socket.socket()')",  # Побочный эффект импортируемого кода.
        "type('Provider', (), {'__init__': lambda self: socket.socket()})()",
    ],
)
def test_network_fixture_detects_imports_and_constructors(operation: str) -> None:
    script = f"""
import socket
import tempfile
from pathlib import Path
from pytest import MonkeyPatch
from tests.conftest import no_network
patch = MonkeyPatch()
guard = no_network.__wrapped__(patch, Path(tempfile.mkdtemp()))
next(guard)
{operation}
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=10
    )
    assert result.returncode != 0
    assert "Failed: " in result.stderr and "forbidden" not in result.stdout


def test_no_ai_imports_at_module_scope() -> None:
    for path in Path("app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))

        def visit(node: ast.AST, path: Path = path) -> None:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                return
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            assert not any(name.split(".")[0] in {"llm_rag", "gigachat"} for name in names), path
            for child in ast.iter_child_nodes(node):
                visit(child)

        visit(tree)


def test_api_and_template_card_without_ai_or_sdk() -> None:
    # Свежий процесс важен: уже импортированные модули скрыли бы регрессию.
    script = """
import sys
import tempfile
from pathlib import Path
sys.modules['llm_rag'] = None
sys.modules['gigachat'] = None
from pytest import MonkeyPatch
from tests.conftest import no_network
patch = MonkeyPatch()
guard = no_network.__wrapped__(patch, Path(tempfile.mkdtemp()))
next(guard)
try:
    from app.main import create_app
    from app.api.health import health
    from app.config import AISettings
    from app.services.ai_adapter import AIAdapter
    from app.services import analysis
    from app.api.applications import _card
    from app.schemas.application_api import ApplicationCard
    from tests.test_ai_analysis import MemorySession, demo_application
    from app.services import ml2_analysis
    application, snapshot = demo_application()
    session = MemorySession(application)
    patch.setattr(analysis, '_load_snapshot', lambda _: snapshot)
    patch.setattr(analysis, 'AIAdapter', lambda: AIAdapter(AISettings(
        mode='template', registry_path='unused', clinical_cache_dir='unused', live_sources=True)))
    app = create_app()
    assert '/health' in app.openapi()['paths']
    assert health().status == 'ok' 
    result = analysis.analyze_application(session, application.id)
    card = ApplicationCard.model_validate(_card(result).model_dump())
    assert card.application.status == 'analyzed'
    assert card.application.traffic_light == 'green'
    assert len(card.items) == 3 and card.disclaimer
    assert all(item.checks and item.checks[0].category for item in card.items)
    assert not any(name.startswith(('llm_rag.', 'gigachat.')) for name in sys.modules)
finally:
    try:
        next(guard)
    except StopIteration:
        pass
    patch.undo()
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, "Изолированный запуск API/template не прошёл."


@pytest.mark.parametrize("mode", ["template", "offline"])
def test_no_provider_created_in_local_modes(mode: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import ml2_analysis, standard_analysis

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("Создан сетевой провайдер в локальном режиме.")

    monkeypatch.setattr(ml2_analysis, "create_provider", forbidden)
    monkeypatch.setattr(standard_analysis, "create_provider", forbidden)
    check = PairCheck(category="А", source="Правило", reference_version="test")
    result = ai_worker.execute(
        {
            "pair": AIInput(drug_name="Тест", mnn="Тест", diagnosis="Тест").model_dump(),
            "local": check.model_dump(mode="json"),
            "settings": AISettings(mode=mode).model_dump(),
            "timeout": 5,
        }
    )
    assert result.error is None and result.check["category"] == "А"


@pytest.mark.parametrize("mode", ["template", "offline"])
def test_worker_guard_installed_before_optional_import(mode: str) -> None:
    script = """
import socket
from app.services import ai_worker
blocked = []
def unexpected_import(*args, **kwargs):
    try:
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    except PermissionError:
        blocked.append(True)
        raise
    raise AssertionError('socket was opened')
ai_worker.run_module = unexpected_import
result = ai_worker.execute({
    'settings': {'mode': MODE, 'live_sources': True},
    'pair': {'drug_name': 'test', 'diagnosis': 'test'},
    'local': {}, 'timeout': 1,
}, guard_network=True)
assert result.error == 'AI_UNAVAILABLE'
assert blocked == [True]
""".replace("MODE", repr(mode))
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, "Worker открыл сетевой сокет до запрета."
