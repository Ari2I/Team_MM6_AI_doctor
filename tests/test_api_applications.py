"""Интеграционные проверки API заявок на PostgreSQL из docker compose."""

import os
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.applications import get_session
from app.main import app
from app.models import Application
from tests.reference_demo import import_demo

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_POSTGRES_TESTS") != "1",
    reason="Тесты API требуют PostgreSQL из docker compose и RUN_POSTGRES_TESTS=1.",
)
DATA_DIR = Path(__file__).parent / "data"


@pytest.fixture()
def client():
    from app.db.session import engine

    with engine.connect() as connection, connection.begin() as transaction:
        with Session(
            bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
        ) as session:
            import_demo(session)
            app.dependency_overrides[get_session] = lambda: session
            try:
                with TestClient(app) as test_client:
                    test_client.db_session = session
                    yield test_client
            finally:
                app.dependency_overrides.pop(get_session, None)
        transaction.rollback()


def test_upload_and_get_card(client: TestClient) -> None:
    with (DATA_DIR / "sample_ok.xlsx").open("rb") as source:
        response = client.post(
            "/applications",
            files={
                "file": (
                    "sample_ok.xlsx",
                    source,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
    assert response.status_code == 201
    body = response.json()
    assert body["application"]["status"] == "analyzed"
    assert body["disclaimer"]
    assert body["items"]
    for item in body["items"]:
        assert item["checks"]
        for check in item["checks"]:
            assert (check["category"] and check["source"] and check["reference_version"]) or (
                check["needs_review"] and check["review_reason"]
            )
    loaded = client.get(f"/applications/{body['application']['id']}")
    assert loaded.status_code == 200
    assert loaded.json() == body


def test_upload_with_diagnosis_form_and_list(client: TestClient) -> None:
    with (DATA_DIR / "sample_ok.xlsx").open("rb") as source:
        response = client.post(
            "/applications",
            files={"file": ("sample_ok.xlsx", source)},
            data={"diagnoses": ["Учебный диагноз 01", "Учебный диагноз 02"]},
        )
    assert response.status_code == 201
    assert set(response.json()["diagnoses"]) >= {"Учебный диагноз 01", "Учебный диагноз 02"}
    assert client.get("/applications?limit=1").status_code == 200


@pytest.mark.parametrize(
    ("filename", "expected_code"),
    [
        ("sample_missing_column.xlsx", "missing_column"),
        ("sample_corrupt.xlsx", "invalid_excel"),
        ("sample_ok.txt", "invalid_extension"),
    ],
)
def test_invalid_upload_is_not_saved(client: TestClient, filename: str, expected_code: str) -> None:
    path = DATA_DIR / ("sample_ok.xlsx" if filename.endswith(".txt") else filename)
    before = client.db_session.scalar(select(func.count()).select_from(Application))
    response = client.post("/applications", files={"file": (filename, path.read_bytes())})
    assert response.status_code == 422
    assert response.json()["errors"][0]["code"] == expected_code
    assert client.db_session.scalar(select(func.count()).select_from(Application)) == before


def workbook_variant(*, remove_name: bool = False, diagnoses: bool = False) -> bytes:
    workbook = load_workbook(DATA_DIR / "sample_ok.xlsx")
    sheet = workbook.active
    if diagnoses:
        sheet.delete_cols(5)
    if remove_name:
        sheet.delete_cols(1)
    buffer = BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()


@pytest.mark.parametrize("diagnosis_count", [1, 2])
def test_two_diagnoses_only_in_form(client: TestClient, diagnosis_count: int) -> None:
    response = client.post(
        "/applications",
        files={"file": ("form.xlsx", workbook_variant(diagnoses=True))},
        data={"diagnoses": ["Учебный диагноз 01", "Учебный диагноз 02"][:diagnosis_count]},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["application"]["status"] == "analyzed"
    assert len(body["diagnoses"]) == diagnosis_count
    assert all(len(item["checks"]) == diagnosis_count for item in body["items"])


def test_list_order_and_pagination(client: TestClient) -> None:
    for _ in range(2):
        response = client.post(
            "/applications",
            files={"file": ("sample_ok.xlsx", (DATA_DIR / "sample_ok.xlsx").read_bytes())},
        )
        assert response.status_code == 201
    entries = client.get("/applications").json()
    assert len(entries) >= 2
    assert [entry["created_at"] for entry in entries] == sorted(
        [entry["created_at"] for entry in entries], reverse=True
    )
    assert client.get("/applications?limit=1&offset=1").json() == entries[1:2]
    assert set(entries[0]) == {
        "id",
        "filename",
        "status",
        "traffic_light",
        "requires_review",
        "created_at",
    }


def test_without_drug_column_and_oversized(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import get_validation_settings

    before = client.db_session.scalar(select(func.count()).select_from(Application))
    response = client.post(
        "/applications", files={"file": ("missing.xlsx", workbook_variant(remove_name=True))}
    )
    assert response.status_code == 422
    assert any(error["column"] == "drug_name" for error in response.json()["errors"])
    monkeypatch.setattr(get_validation_settings(), "max_excel_size_bytes", 8)
    response = client.post("/applications", files={"file": ("big.xlsx", b"x" * 9)})
    assert response.status_code == 422
    assert response.json()["errors"][0]["code"] == "file_too_large"
    assert client.db_session.scalar(select(func.count()).select_from(Application)) == before


def test_request_errors_are_sanitized(client: TestClient) -> None:
    for response in [
        client.post("/applications"),
        client.get("/applications/invalid"),
        client.get("/applications?limit=101"),
    ]:
        assert response.status_code == 422
        assert "errors" in response.json()


def test_missing_application_is_404(client: TestClient) -> None:
    response = client.get("/applications/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_download_report(client: TestClient) -> None:
    created = client.post(
        "/applications",
        files={"file": ("sample_ok.xlsx", (DATA_DIR / "sample_ok.xlsx").read_bytes())},
    )
    assert created.status_code == 201
    card = created.json()
    application_id = card["application"]["id"]
    response = client.get(f"/applications/{application_id}/report.xlsx")
    assert response.status_code == 200
    assert response.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert response.headers["content-disposition"] == (
        f'attachment; filename="application-{application_id}.xlsx"'
    )
    workbook = load_workbook(BytesIO(response.content))
    assert workbook.sheetnames == ["Сводка", "Препараты", "Предупреждения"]
    assert workbook["Препараты"].max_row - 1 == sum(len(item["checks"]) for item in card["items"])
    assert client.get(f"/applications/{application_id}").json() == card
