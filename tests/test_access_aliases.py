"""Новые имена флагов сохраняют совместимость с прежними настройками."""

import pytest
from fastapi.testclient import TestClient

from app.config import AccessSettings
from app.main import create_app


@pytest.mark.parametrize("enabled", [False, True])
def test_access_aliases_take_precedence(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> None:
    monkeypatch.setenv("DOCS_ENABLED", str(enabled))
    monkeypatch.setenv("LIST_ENDPOINT_ENABLED", str(enabled))
    monkeypatch.setenv("ENABLE_API_DOCS", str(not enabled))
    monkeypatch.setenv("ENABLE_APPLICATION_LIST", str(not enabled))
    settings = AccessSettings(_env_file=None)
    assert settings.enable_api_docs is enabled
    assert settings.enable_application_list is enabled
    with TestClient(create_app()) as client:
        assert client.get("/docs").status_code == (200 if enabled else 404)
        if not enabled:
            assert client.get("/applications").status_code == 404
        assert client.get("/health").status_code == 200
