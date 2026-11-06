"""Контракт демонстрационного прохода через API и БД."""

from io import BytesIO
import json
import os
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from openpyxl import load_workbook
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.main import app


ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "demo.xlsx"


@pytest.fixture
def client(monkeypatch):
    from app.api import applications
    from app.db.base import Base
    from app.services.reference_seed import load_bundle, seed_reference

    url = os.getenv("DEMO_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Для интеграционного теста задайте DEMO_TEST_DATABASE_URL")
    engine = create_engine(url)
    schema = f"test_demo_{uuid4().hex}"
    with engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA {schema}"))
    test_engine = engine.execution_options(
        schema_translate_map={None: schema}
    )
    sessions = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)
    try:
        Base.metadata.create_all(test_engine)
        with sessions.begin() as session:
            seed_reference(session, load_bundle())
            seed_reference(session, load_bundle())
        monkeypatch.setattr(applications, "sessions", lambda: sessions)
        with TestClient(app) as test_client:
            yield test_client
    finally:
        with engine.begin() as conn:
            conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        engine.dispose()


def test_application_routes_exist():
    assert "/api/applications" in app.openapi()["paths"]


def test_demo_roundtrip(client):
    response = client.post("/api/applications", files={"file": ("demo.xlsx", EXAMPLE.read_bytes())})
    assert response.status_code == 201, response.text
    result = response.json()
    expected = json.loads((ROOT / "examples" / "expected.json").read_text(encoding="utf-8"))
    assert result["known_total"] == expected["known_total"]
    assert len(result["items"]) == expected["item_count"]
    assert len(result["checks"]) == 8
    assert result["requires_review"] is True
    assert all(check["needs_review"] for check in result["checks"])
    rows = {item["item_id"]: item["row_number"] for item in result["items"]}
    diagnoses = {diag["diagnosis_id"]: diag["name"] for diag in result["diagnoses"]}
    observed = [
        [rows[c["item_id"]], diagnoses[c["diagnosis_id"]], c["category"], c["reason_code"],
         c["citations"][0]["fragment_id"] if c["citations"] else None]
        for c in result["checks"]
    ]
    assert sorted(observed) == sorted(expected["checks"])
    assert result['reference_version'] == expected['reference_version']
    assert result['corpus_version'] == expected['corpus_version']
    conditional = [c for c in result['checks'] if c['reason_code'] == 'MISSING_MEDICAL_CONTEXT']
    assert len(conditional) == 2
    assert all(not c['citations'] and c['review_sources'][0]['fragment_id'] == 'amlodipine-e11-context'
               for c in conditional)
    route = f'/api/applications/{result["application_id"]}'
    assert client.get(route).json() == result
    workbook = load_workbook(BytesIO(client.get(route + "/result.xlsx").content))
    assert workbook.sheetnames == ["Заявка", "Проверка", "Источники", "Сводка"]
    html = client.get(route + "/report.html").text
    assert "1 000,00" in html
    assert "amlodipine-e11-context" in html
    assert "Материал для сверки" in {cell.value for cell in workbook["Источники"]["K"]}


def test_changed_demo_is_rejected_before_external_call(client):
    response = client.post("/api/applications", data={"mode": "gigachat"},
                           files={"file": ("demo.xlsx", EXAMPLE.read_bytes() + b"extra")})
    assert response.status_code == 422
    assert response.json()["detail"]["issues"][0]["code"] == "unapproved_demo_file"


def test_bad_input_has_structured_issues(client):
    response = client.post("/api/applications", files={"file": ("bad.xlsx", b"bad")})
    assert response.status_code == 422
    assert response.json()["detail"]["issues"]
