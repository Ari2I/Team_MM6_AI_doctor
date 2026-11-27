"""AI-книга → HTTP → отдельный worker → JSONB/item_checks → повторный GET/XLSX."""

import os
import sys
from io import BytesIO
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import func, select

from app.api import applications
from app.config import CATEGORY_LABELS
from app.main import app
from app.models import Application, ApplicationAIResult, ItemCheck
from app.services import integration_client
from app.services.integration_mapping import prepare_integration_request
from tests.ai_workbook_data import workbook_bytes
from tests.test_ai_reports_postgres import session as session
from tests.test_defense_analysis import defense_settings as defense_settings

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_POSTGRES_TESTS") != "1", reason="Нужен PostgreSQL и RUN_POSTGRES_TESTS=1"
)


@pytest.fixture
def client(session, defense_settings, monkeypatch):
    monkeypatch.setattr(applications, "AISettings", lambda: defense_settings)
    app.dependency_overrides[applications.get_session] = lambda: session
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(applications.get_session, None)


@pytest.mark.parametrize("layout", [2, 4])
def test_structured_upload_actual_worker_reopen_and_export(client, session, monkeypatch, layout):
    original_run = integration_client.subprocess.run
    calls = []

    def run(command, **kwargs):
        assert command == [sys.executable, "-m", "app.services.integration_worker"]
        calls.append(True)
        return original_run([sys.executable, "-m", "tests.synthetic_integration_worker"], **kwargs)

    monkeypatch.setattr(integration_client.subprocess, "run", run)
    response = client.post(
        "/applications",
        files={"file": ("synthetic.xlsx", workbook_bytes(layout))},
        data={"age_group": "adult"},
    )
    assert response.status_code == 201
    card = response.json()
    assert card["application"]["status"] == "analyzed"
    identifier = UUID(card["application"]["id"])
    session.expire_all()
    saved = session.get(Application, identifier)
    assert saved.integration_input is not None
    assert saved.integration_input["diagnoses"][0]["conditions"]["age_years"] == "38"
    assert saved.integration_input["diagnoses"][0]["recommendation_edition"] == "359_3"
    assert next(i for i in saved.items if i.row_number == 3).amount.as_tuple().exponent == -3
    assert prepare_integration_request(saved, currency="EUR").payload == saved.integration_input
    expected = 2 if layout == 2 else 3
    assert len(calls) == 1
    checks = [c for i in card["items"] for c in i["checks"]]
    assert len(checks) == expected and all(c["category"] == "А" for c in checks)
    assert all("demo-api-2" not in c["source"] for c in checks)
    stored = session.get(ApplicationAIResult, identifier)
    assert stored.result["request"] == saved.integration_input
    assert len(stored.result["pairs"]) == expected
    assert all(p["classification_diagnostics"] is not None for p in stored.result["pairs"])
    assert all(p["generation_mode"] == "MODEL" for p in stored.result["pairs"])
    # Повторная задача заменяет текущие проверки, не удваивает пары.
    applications.analyze_application(session, identifier)
    assert len(calls) == 2
    count = session.scalar(
        select(func.count())
        .select_from(ItemCheck)
        .where(
            ItemCheck.item_id.in_([i.id for i in saved.items]),
        )
    )
    assert count == expected
    new_card = client.get(f"/applications/{identifier}").json()
    monkeypatch.setattr(
        integration_client.subprocess, "run", lambda *a, **k: pytest.fail("Повтор worker")
    )
    monkeypatch.setitem(sys.modules, "llm_rag", None)
    monkeypatch.setitem(sys.modules, "gigachat", None)
    assert client.get(f"/applications/{identifier}").json() == new_card
    assert client.get(f"/applications/{identifier}/ai-report").status_code == 200
    excel = client.get(f"/applications/{identifier}/report.xlsx")
    assert excel.status_code == 200
    book = load_workbook(BytesIO(excel.content), read_only=True)
    try:
        rows = list(book["Препараты"].values)[1:]
        assert len(rows) == expected
        assert all(row[4] == CATEGORY_LABELS["А"] and "Цитата:" in row[9] for row in rows)
    finally:
        book.close()


@pytest.mark.parametrize(
    "options",
    [
        {"age": "8", "group": "adult"},
        {"links": [["unknown", "d1"]]},
        {"links": [["i1", "d1"], ["i1", "d1"]]},
        {"formula": True},
    ],
)
def test_invalid_structured_input_is_not_saved_or_sent(client, session, monkeypatch, options):
    monkeypatch.setattr(
        integration_client.subprocess, "run", lambda *a, **k: pytest.fail("Worker invalid input")
    )
    before = session.scalar(select(func.count()).select_from(Application))
    response = client.post(
        "/applications", files={"file": ("synthetic.xlsx", workbook_bytes(**options))}
    )
    assert response.status_code == 422
    assert response.json()["errors"][0]["code"] == "parse_issue"
    assert session.scalar(select(func.count()).select_from(Application)) == before
