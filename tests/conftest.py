"""Размещение временных файлов тестов в общей локальной папке проекта."""

from pathlib import Path


def pytest_configure(config):
    if config.option.basetemp is None:
        temporary_root = Path(config.rootpath) / ".local" / "tmp"
        temporary_root.mkdir(parents=True, exist_ok=True)
        config.option.basetemp = str(temporary_root / "pytest")
