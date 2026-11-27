"""HTTP/JSONB/XLSX сборки защиты при активном demo-api-2, без реального AI."""

import os
import sys
from io import BytesIO
from pathlib import Path
from uuid import UUID

import pytest
from openpyxl import load_workbook

from app.api.applications import get_session
from app.config import CATEGORY_LABELS
from app.models import Application, ApplicationAIResult
from app.services import analysis, application_ai
from app.services.integration_protocol import IntegrationReply
from tests.defense_data import defense_result
from tests.test_defense_analysis import defense_settings as defense_settings

pytest_plugins = ["tests.test_demo_v2"]
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_POSTGRES_TESTS") != "1", reason="Нужен PostgreSQL и RUN_POSTGRES_TESTS=1"
)


def upload(client):
    return client.post(
        "/applications",
        files={"file": ("correct.xlsx", Path("data/demo/correct.xlsx").read_bytes())},
        data={"age_group": "adult"},
    )


@pytest.mark.parametrize(
    "mode", ["evidence", "approved", "none", "conflict", "partial", "fallback", "generation_error"]
)
def test_card_excel_and_report_use_only_facade(
    mode, demo_client, defense_settings, monkeypatch, tmp_path
):
    from tests.conftest import no_network

    session = demo_client.app.dependency_overrides[get_session]()
    calls = []

    def worker(self, prepared):
        application = session.get(Application, UUID(prepared.payload["request_id"]))
        with pytest.MonkeyPatch.context() as patch:
            guard = no_network.__wrapped__(patch, tmp_path)
            next(guard)
            try:
                calls.append(prepared.payload)
                return IntegrationReply(
                    request_id=application.id,
                    outcome="result",
                    result=defense_result(application, mode),
                )
            finally:
                with pytest.raises(StopIteration):
                    next(guard)

    monkeypatch.setattr(application_ai.IntegrationWorkerClient, "analyze", worker)
    response = upload(demo_client)
    assert response.status_code == 201 and len(calls) == 1
    card = response.json()
    identifier = UUID(card["application"]["id"])
    assert card["application"]["status"] == "analyzed"
    assert card["application"]["reference_is_synthetic"] is False
    assert card["application"]["reference_name"] == "ГРЛС и клинические рекомендации"
    assert card["application"]["requires_review"]
    assert card["application"]["category_shares"]["total_amount"] == 1000
    checks = [c for item in card["items"] for c in item["checks"]]
    expected = ["Б", "Б", "Б"]
    if mode == "approved":
        expected[0] = "В"
    elif mode in {"conflict", "partial"}:
        expected[0] = None
    elif mode in {"none", "fallback"}:
        expected = [None, None, None]
    assert [c["category"] for c in checks] == expected
    assert all(c["source"].startswith("ИИ-анализ:") and c["needs_review"] for c in checks)
    assert all("demo-api-2" not in str(c) for c in checks)
    assert all(not p.get("inn") for p in calls[0]["purchases"])
    assert all(d["conditions"]["age_group"] == "adult" for d in calls[0]["diagnoses"])
    session.expire_all()
    stored = session.get(ApplicationAIResult, identifier)
    assert stored is not None and stored.result["request"]["request_id"] == str(identifier)
    assert all(c["backend_rule"]["category"] is None for c in stored.checks)
    assert all(c["backend_rule"]["reference_version"] is None for c in stored.checks)
    expected_human_report = stored.result["human_report"]
    assert expected_human_report["summary"] in card["application"]["summary"]
    if mode not in {"partial", "fallback"}:
        citation = stored.result["pairs"][0]["citations"][0]
        assert checks[0]["comment"].endswith(" Цитата: " + citation["quote"])
        assert citation["url"] in checks[0]["source"]
    monkeypatch.setattr(
        application_ai.IntegrationWorkerClient, "analyze", lambda *a: pytest.fail("Повтор сети")
    )
    monkeypatch.setitem(sys.modules, "llm_rag", None)
    monkeypatch.setitem(sys.modules, "gigachat", None)
    assert demo_client.get(f"/applications/{identifier}").json() == card
    report = demo_client.get(f"/applications/{identifier}/ai-report")
    assert report.status_code == 200 and report.json()["human_report"] == expected_human_report
    download = demo_client.get(f"/applications/{identifier}/report.xlsx")
    assert download.status_code == 200
    workbook = load_workbook(BytesIO(download.content))
    rows = list(workbook["Препараты"].values)[1:]
    assert len(rows) == len(checks)
    for row, check in zip(rows, checks, strict=True):
        assert row[4] == (CATEGORY_LABELS[check["category"]] if check["category"] else None)
        assert row[5] == "Требует проверки"
        assert row[7] == check["source"] and row[8] == check["reference_version"]
        assert row[9] == check["comment"]


@pytest.mark.parametrize("reason", ["AI_TIMEOUT", "AI_UNAVAILABLE", "AI_INVALID_RESPONSE"])
def test_failed_reanalysis_does_not_restore_demo_categories(
    reason, demo_client, defense_settings, monkeypatch
):
    session = demo_client.app.dependency_overrides[get_session]()

    def worker(self, prepared):
        application = session.get(Application, UUID(prepared.payload["request_id"]))
        return IntegrationReply(
            request_id=application.id, outcome="result", result=defense_result(application)
        )

    monkeypatch.setattr(application_ai.IntegrationWorkerClient, "analyze", worker)
    response = upload(demo_client)
    assert response.status_code == 201
    identifier = UUID(response.json()["application"]["id"])
    assert session.get(ApplicationAIResult, identifier) is not None
    monkeypatch.setattr(
        application_ai.IntegrationWorkerClient,
        "analyze",
        lambda self, prepared: IntegrationReply(
            request_id=identifier, outcome="error", error=reason
        ),
    )
    assert analysis.analyze_application(session, identifier).status == "analyzed"
    session.expire_all()
    assert session.get(ApplicationAIResult, identifier) is None
    assert demo_client.get(f"/applications/{identifier}/ai-report").status_code == 409
    card = demo_client.get(f"/applications/{identifier}").json()
    assert card["application"]["reference_is_synthetic"] is None
    assert card["application"]["category_shares"]["undetermined"] == 100
    assert all(item["mnn"] is None for item in card["items"])
    assert all(
        c["category"] is None and c["review_reason"] == reason
        for item in card["items"]
        for c in item["checks"]
    )
    workbook = load_workbook(
        BytesIO(demo_client.get(f"/applications/{identifier}/report.xlsx").content)
    )
    assert all(
        row[4] is None and row[5] == "Требует проверки"
        for row in list(workbook["Препараты"].values)[1:]
    )


@pytest.mark.parametrize("reanalysis", [False, True])
def test_ai_only_report_and_card_remain_atomic(
    reanalysis, demo_client, defense_settings, monkeypatch
):
    session = demo_client.app.dependency_overrides[get_session]()
    identities = []

    def worker(self, prepared):
        application = session.get(Application, UUID(prepared.payload["request_id"]))
        identities.append(application.id)
        return IntegrationReply(
            request_id=application.id, outcome="result", result=defense_result(application)
        )

    monkeypatch.setattr(application_ai.IntegrationWorkerClient, "analyze", worker)
    if reanalysis:
        assert upload(demo_client).status_code == 201

    def broken_summary(*args):
        raise RuntimeError("PRIVATE-ERROR-MARKER")

    monkeypatch.setattr(analysis, "build_summary", broken_summary)
    if reanalysis:
        result = analysis.analyze_application(session, identities[0])
        assert result.status == "error" and len(identities) == 2
    else:
        assert upload(demo_client).json()["application"]["status"] == "error"
    session.expire_all()
    assert session.get(ApplicationAIResult, identities[0]) is None
    assert demo_client.get(f"/applications/{identities[0]}/ai-report").status_code == 409
    response = demo_client.get(f"/applications/{identities[0]}")
    assert "PRIVATE-ERROR-MARKER" not in response.text
    card = response.json()
    assert card["application"]["category_shares"] is None
    assert all(not item["checks"] and item["mnn"] is None for item in card["items"])
