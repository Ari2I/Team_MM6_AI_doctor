"""Общие ограничения для тестов локальных режимов AI."""

import socket
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def isolated_ai_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Настройки рабочего Compose не выбирают путь анализа внутри unit-теста.

    Тесты задают режим/ресурсы явно через AISettings или monkeypatch. Отдельный
    RAG_TEST_DATA_DIR сохраняется: это явное разрешение проверять рабочий кэш.
    Значения окружения не выводятся и не переносятся в тестовые артефакты.
    """
    for name in (
        "AI_MODE",
        "AI_CORPUS_PATH",
        "AI_REGISTRY_PATH",
        "AI_CLINICAL_CACHE_DIR",
        "AI_LIVE_SOURCES",
        "AI_PAIR_TIMEOUT_SECONDS",
        "AI_APPLICATION_TIMEOUT_SECONDS",
        "AI_CURRENCY",
        "AI_BACKEND_REVISION",
        "AI_LIBRARY_REVISION",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Запретить сетевые сокеты, DNS и соединения, включая проглоченные ошибки.

    Запрещены и сетевые, и Unix-сокеты. Проверка старта приложения не
    создаёт event loop TestClient. Worker дополнительно защищён audit hook.
    """
    attempts: list[str] = []
    original_socket = socket.socket
    original_run = subprocess.run
    marker = tmp_path / "worker-network-attempt"

    def guarded_run(command: Any, *args: Any, **kwargs: Any) -> Any:
        if (
            isinstance(command, list | tuple)
            and len(command) == 3
            and command[1] == "-m"
            and command[2] in {"app.services.ai_worker", "app.services.integration_worker"}
        ):
            bootstrap = f"""
import sys
from pathlib import Path
def forbid(event, args):
    if event in {{'socket.__new__', 'socket.connect', 'socket.getaddrinfo', 'socket.sendto'}}:
        Path({str(marker)!r}).touch()
        raise PermissionError('network forbidden by test')
sys.addaudithook(forbid)
import runpy
runpy.run_module({command[2]!r}, run_name='__main__')
"""
            command = [command[0], "-c", bootstrap]
        return original_run(command, *args, **kwargs)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        attempts.append("network")
        pytest.fail("В локальном режиме запрещён сетевой вызов.")

    class LocalSocket(original_socket):
        def __init__(self, family: int = socket.AF_INET, *args: Any, **kwargs: Any) -> None:
            forbidden()

        def connect(self, *args: Any, **kwargs: Any) -> Any:
            return forbidden()

        def connect_ex(self, *args: Any, **kwargs: Any) -> Any:
            return forbidden()

        def sendto(self, *args: Any, **kwargs: Any) -> Any:
            return forbidden()

    monkeypatch.setattr(socket, "socket", LocalSocket)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "run", guarded_run)
    yield
    assert not attempts, "Обнаружена попытка сети, даже если вызывающий код подавил исключение."
    assert not marker.exists(), "Worker попытался обратиться к сети."
