"""Официальный профиль использует загруженный снимок через обычный HTTP API."""

from io import BytesIO
from uuid import UUID

from openpyxl import Workbook, load_workbook

from app.api import applications
from app.services import official_reference
from app.services.analysis import analyze_application
from app.services.application_io import saved_checks
from examples.generate_independent_applications import generate
from tests.backend.test_demo import client
from tests.backend.test_official_reference import make_reference


def test_official_http_result_is_saved_and_exported(client, monkeypatch, tmp_path):
    snapshot = tmp_path / "external-reference.json"
    snapshot.write_text(make_reference().model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(applications, "official_path", lambda: snapshot)
    monkeypatch.setattr(official_reference, "official_path", lambda: snapshot)

    assert client.get("/api/references").json()["default"] == "official"
    book = Workbook()
    book.active.append(["Торговое наименование", "Диагноз", "Сумма", "Дозировка", "Количество"])
    book.active.append(["Независимый тестовый препарат", "G43.9", 900, None, 1])
    book.active.append(["Вымышленное неизвестное 20261107", None, 0.25, None, 1])
    book.active.append(["Независимый тестовый препарат", "Z99.9", 99.75, None, 1])
    buffer = BytesIO()
    book.save(buffer)
    upload = {"file": ("любое-имя.xlsx", buffer.getvalue())}
    response = client.post("/api/applications", files=upload)
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["reference_profile"] == "official"
    assert result["is_demo"] is False
    assert result["known_total"] == "1000.00"
    assert len(result["checks"]) == 3
    assert all(row["category"] is None and row["needs_review"] for row in result["checks"])
    assert sum(row["relationship_found"] for row in result["checks"]) == 1
    assert result["items"][0]["official_drug"]["compositions"][0]["registration"] == "TEST-1"
    assert {row["reason_code"] for row in result["checks"]} == {
        "OFFICIAL_LINK_REQUIRES_REVIEW", "OFFICIAL_DIAGNOSIS_NOT_INDEXED", "OFFICIAL_DRUG_NOT_FOUND",
    }
    base = f'/api/applications/{result["application_id"]}'
    assert client.get(base).json() == result
    assert "Состав и реестр" in load_workbook(BytesIO(client.get(base + "/result.xlsx").content)).sheetnames
    assert "Реестр и состав" in client.get(base + "/report.html").text
    with applications.sessions().begin() as session:
        application_id = UUID(result['application_id'])
        analyze_application(session, application_id, commit=False)
        assert len(saved_checks(session, application_id)) == 3


def test_generator_needs_no_registry_and_is_reproducible(tmp_path, monkeypatch):
    monkeypatch.setenv("OFFICIAL_REFERENCE_PATH", str(tmp_path / "absent.json"))
    first = generate(tmp_path / "first", 42, 2)
    second = generate(tmp_path / "second", 42, 2)
    read = lambda path: list(load_workbook(path, read_only=True).active.values)
    assert list(map(read, first)) == list(map(read, second))
    assert all(len(read(path)) == 7 for path in first)
