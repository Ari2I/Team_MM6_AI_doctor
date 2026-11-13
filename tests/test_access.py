"""Защитные настройки работают без подключения к PostgreSQL."""

import pytest
from fastapi.testclient import TestClient

from app.config import AccessSettings
from app.main import create_app


@pytest.mark.parametrize("enabled", [False, True])
def test_documentation_switch(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> None:
    monkeypatch.setenv("ENABLE_API_DOCS", str(enabled))
    with TestClient(create_app()) as client:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert client.get(path).status_code == (200 if enabled else 404)


def test_list_disabled_before_database_access(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_APPLICATION_LIST", "false")
    with TestClient(create_app()) as client:
        assert client.get("/applications").status_code == 404
        assert client.get("/applications/").status_code == 404
        assert client.get("/health").status_code == 200


def test_safe_defaults() -> None:
    settings = AccessSettings(_env_file=None, _env_ignore_empty=True)
    # Explicit input isolates the defaults from developer environment variables.
    assert AccessSettings.model_fields["enable_api_docs"].default is False
    assert AccessSettings.model_fields["enable_application_list"].default is False
    assert isinstance(settings.enable_api_docs, bool)
