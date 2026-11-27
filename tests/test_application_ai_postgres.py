"""Настоящая загрузка/JSONB/повторный GET, worker подменён и защищён от сети."""

import os
import sys
from pathlib import Path
from uuid import UUID

import pytest

from app.api.applications import get_session
from app.config import AISettings
from app.models import Application, ApplicationAIResult
from app.services import analysis, application_ai
from app.services.ai_adapter import AIAdapter
from app.services.integration_protocol import IntegrationReply
from tests.ai_report_data import integration_result

pytest_plugins = ["tests.test_demo_v2"]
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_POSTGRES_TESTS") != "1", reason="Нужен PostgreSQL и RUN_POSTGRES_TESTS=1"
)


@pytest.fixture
def library_path(monkeypatch):
    source = Path("ai/src").resolve()
    if not source.is_dir():
        pytest.skip("AI library is absent")
    monkeypatch.syspath_prepend(str(source))
    monkeypatch.setitem(sys.modules, "gigachat", None)


@pytest.mark.parametrize("mode", ["complete", "partial", "fallback", "evidence"])
def test_upload_saves_ai_report_and_reopens_without_worker(
    mode, demo_client, library_path, monkeypatch, tmp_path
):
    from tests.conftest import no_network

    config = AISettings(mode="network", backend_revision="a" * 40, library_revision="b" * 40)
    monkeypatch.setattr(analysis, "AIAdapter", lambda: AIAdapter(config))
    session = demo_client.app.dependency_overrides[get_session]()
    calls = []

    def worker(self, prepared):
        application = session.get(Application, UUID(prepared.payload["request_id"]))
        # БД и TestClient используют сокеты. Сам подменённый фасад проверяем
        # отдельным запретом после чтения заявки, до генерации результата.
        with pytest.MonkeyPatch.context() as patch:
            guard = no_network.__wrapped__(patch, tmp_path)
            next(guard)
            try:
                calls.append(prepared)
                value = integration_result(application, mode)
                return IntegrationReply(request_id=application.id, outcome="result", result=value)
            finally:
                with pytest.raises(StopIteration):
                    next(guard)

    monkeypatch.setattr(application_ai.IntegrationWorkerClient, "analyze", worker)
    monkeypatch.setattr(AIAdapter, "analyze", lambda *a: pytest.fail("Попарный network"))
    response = demo_client.post(
        "/applications",
        files={"file": ("correct.xlsx", Path("data/demo/correct.xlsx").read_bytes())},
        data={"age_group": "adult"},
    )
    assert response.status_code == 201 and len(calls) == 1
    card = response.json()
    identifier = card["application"]["id"]
    assert card["application"]["status"] == "analyzed"
    assert card["application"]["category_shares"]["total_amount"] == 1000
    assert card["application"]["requires_review"]
    assert [i["checks"][0]["category"] for i in card["items"]] == ["А", "Б", "В"]
    assert all(i["checks"][0]["reference_version"] == "demo-api-2" for i in card["items"])
    session.expire_all()
    stored = session.get(ApplicationAIResult, UUID(identifier))
    assert stored is not None
    assert all(
        d["conditions"]["age_group"] == "adult" for d in stored.result["request"]["diagnoses"]
    )
    assert stored.result["completeness"] == ("PARTIAL" if mode == "partial" else "COMPLETE")
    if mode == "evidence":
        assert stored.result["evidence_snapshot"]["documents"]
    expected = stored.result["human_report"]
    monkeypatch.setattr(
        application_ai.IntegrationWorkerClient, "analyze", lambda *a: pytest.fail("Повтор сети")
    )
    monkeypatch.setitem(sys.modules, "llm_rag", None)
    monkeypatch.setitem(sys.modules, "gigachat", None)
    first = demo_client.get(f"/applications/{identifier}/ai-report")
    second = demo_client.get(f"/applications/{identifier}/ai-report")
    assert first.status_code == 200 and first.json() == second.json()
    assert first.json()["human_report"] == expected
    assert first.json()["backend_revision"] == "a" * 40
    assert first.json()["ai_revision"] == "b" * 40
    assert demo_client.get(f"/applications/{identifier}").json() == card
    assert demo_client.get(f"/applications/{identifier}/report.xlsx").status_code == 200


@pytest.mark.parametrize("reason", ["AI_TIMEOUT", "AI_UNAVAILABLE", "AI_INVALID_RESPONSE"])
def test_failed_reanalysis_clears_old_ai_report_and_keeps_card(
    reason, demo_client, library_path, monkeypatch
):
    config = AISettings(mode="network", backend_revision="a" * 40, library_revision="b" * 40)
    monkeypatch.setattr(analysis, "AIAdapter", lambda: AIAdapter(config))
    session = demo_client.app.dependency_overrides[get_session]()

    def worker(self, prepared):
        application = session.get(Application, UUID(prepared.payload["request_id"]))
        return IntegrationReply(
            request_id=application.id, outcome="result", result=integration_result(application)
        )

    monkeypatch.setattr(application_ai.IntegrationWorkerClient, "analyze", worker)
    response = demo_client.post(
        "/applications",
        files={"file": ("correct.xlsx", Path("data/demo/correct.xlsx").read_bytes())},
    )
    assert response.status_code == 201
    identifier = UUID(response.json()["application"]["id"])
    assert demo_client.get(f"/applications/{identifier}/ai-report").status_code == 200
    monkeypatch.setattr(
        application_ai.IntegrationWorkerClient,
        "analyze",
        lambda self, prepared: IntegrationReply(
            request_id=identifier, outcome="error", error=reason
        ),
    )
    result = analysis.analyze_application(session, identifier)
    assert result.status == "analyzed"
    session.expire_all()
    assert session.get(ApplicationAIResult, identifier) is None
    assert demo_client.get(f"/applications/{identifier}/ai-report").status_code == 409
    card = demo_client.get(f"/applications/{identifier}").json()
    assert [i["checks"][0]["category"] for i in card["items"]] == ["А", "Б", "В"]
    assert all(i["checks"][0]["review_reason"] == reason for i in card["items"])
    assert demo_client.get(f"/applications/{identifier}/report.xlsx").status_code == 200


@pytest.mark.parametrize("reanalysis", [False, True])
def test_report_rolls_back_if_card_cannot_be_committed(
    reanalysis, demo_client, library_path, monkeypatch
):
    config = AISettings(mode="network", backend_revision="a" * 40, library_revision="b" * 40)
    monkeypatch.setattr(analysis, "AIAdapter", lambda: AIAdapter(config))
    session = demo_client.app.dependency_overrides[get_session]()
    identifiers = []

    def worker(self, prepared):
        application = session.get(Application, UUID(prepared.payload["request_id"]))
        identifiers.append(application.id)
        return IntegrationReply(
            request_id=application.id, outcome="result", result=integration_result(application)
        )

    def cannot_build_summary(*args):
        raise RuntimeError("PRIVATE-ERROR-MARKER")

    monkeypatch.setattr(application_ai.IntegrationWorkerClient, "analyze", worker)

    def upload():
        return demo_client.post(
            "/applications",
            files={"file": ("correct.xlsx", Path("data/demo/correct.xlsx").read_bytes())},
        )

    if reanalysis:
        assert upload().status_code == 201
        assert demo_client.get(f"/applications/{identifiers[0]}/ai-report").status_code == 200
        monkeypatch.setattr(analysis, "build_summary", cannot_build_summary)
        analysis.analyze_application(session, identifiers[0])
        response = demo_client.get(f"/applications/{identifiers[0]}")
        assert len(identifiers) == 2 and identifiers[0] == identifiers[1]
    else:
        monkeypatch.setattr(analysis, "build_summary", cannot_build_summary)
        response = upload()
        assert response.status_code == 201 and len(identifiers) == 1
    assert response.json()["application"]["status"] == "error"
    session.expire_all()
    assert session.get(ApplicationAIResult, identifiers[0]) is None
    assert demo_client.get(f"/applications/{identifiers[0]}/ai-report").status_code == 409
    assert "PRIVATE-ERROR-MARKER" not in response.text
