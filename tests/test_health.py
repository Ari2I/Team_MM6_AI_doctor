"""Проверка ответа GET /health без подключения к PostgreSQL."""

from fastapi.testclient import TestClient

from app.main import app


def test_health() -> None:
    """API возвращает успешный статус и рекомендательный дисклеймер."""
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "disclaimer": "Анализ носит рекомендательный характер. Решение принимает человек.",
    }
