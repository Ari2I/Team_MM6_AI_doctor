"""Снимок отчёта: публичные модели AI, безопасные ошибки и чтение без библиотеки."""

import json
import subprocess
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock
from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from app.models import ApplicationAIResult
from app.schemas.analysis import ReferenceSnapshot
from app.services.ai_reports import AIReportError, read_ai_report, result_checks, save_ai_result
from tests.ai_report_data import human_report, integration_result
from tests.test_integration_mapping import make_application

pytestmark = pytest.mark.usefixtures("no_network")


@pytest.fixture
def library_path(monkeypatch: pytest.MonkeyPatch) -> None:
    source = Path("ai/src").resolve()
    if not source.is_dir():
        pytest.skip("AI library is absent")
    monkeypatch.syspath_prepend(str(source))
    monkeypatch.setitem(sys.modules, "gigachat", None)


def stored_report() -> ApplicationAIResult:
    return ApplicationAIResult(
        application_id=UUID(int=100),
        saved_at=datetime.now(UTC),
        backend_revision="a" * 40,
        ai_revision="b" * 40,
        result={
            "schema_version": "integration-result.1",
            "request": {"request_id": str(UUID(int=100))},
            "completeness": "PARTIAL",
            "human_report": human_report(),
        },
        checks=[
            dict(
                number=1,
                item_id=str(UUID(int=11)),
                diagnosis_id=str(UUID(int=1)),
                row_number=2,
                backend_rule={"needs_review": True, "review_reason": "NO_RULE_FOR_PAIR"},
                conflict=False,
                processed=False,
                reasons=["BUDGET_EXHAUSTED"],
            )
        ],
    )


def test_read_preserves_human_report_without_sources_or_library() -> None:
    stored = stored_report()
    before = deepcopy(stored.result)
    report = read_ai_report(stored)
    assert report.human_report.model_dump(mode="json") == before["human_report"]
    assert report.completeness == "PARTIAL" and not report.checks[0].processed
    assert report.checks[0].reasons == ["BUDGET_EXHAUSTED"]
    assert stored.result == before


@pytest.mark.parametrize("damage", ["schema", "request", "human_report", "checks", "revision"])
def test_corrupt_report_is_safe(damage: str) -> None:
    stored = stored_report()
    marker = "PRIVATE-REPORT-MARKER"
    if damage == "schema":
        stored.result["schema_version"] = marker
    elif damage == "request":
        stored.result["request"]["request_id"] = marker
    elif damage == "human_report":
        stored.result["human_report"]["title"] = {"private": marker}
    elif damage == "checks":
        stored.checks[0]["number"] = 2
    else:
        stored.ai_revision = marker
    with pytest.raises(AIReportError) as caught:
        read_ai_report(stored)
    assert marker not in str(caught.value) and marker not in repr(caught.value)


def test_report_read_in_fresh_process_without_ai() -> None:
    script = """
import sys,tempfile
from pathlib import Path
sys.modules['llm_rag']=None
sys.modules['gigachat']=None
from pytest import MonkeyPatch
from tests.conftest import no_network
patch=MonkeyPatch()
guard=no_network.__wrapped__(patch,Path(tempfile.mkdtemp()))
next(guard)
try:
 from app.main import create_app
 from app.api.applications import get_ai_report
 from app.models import Application,ApplicationAIResult
 from tests.test_ai_reports import stored_report
 stored=stored_report()
 class Session:
  def get(self,model,key):
   return stored if model is ApplicationAIResult else Application(id=key)
 response=get_ai_report(stored.application_id,Session())
 assert response.application_id==stored.application_id
 assert len(response.human_report.items)==1
 assert '/applications/{application_id}/ai-report' in create_app().openapi()['paths']
 assert not any(name.startswith(('llm_rag.','gigachat.')) for name in sys.modules)
finally:
 try: next(guard)
 except StopIteration: pass
 patch.undo()
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, "Чтение отчёта требует AI или нарушает запрет сети."


@pytest.mark.parametrize("mode", ["complete", "partial", "fallback"])
def test_result_rows_follow_explicit_ids(mode: str, library_path: None) -> None:
    application = make_application()
    result = integration_result(application, mode)
    before = deepcopy(result)
    checks = result_checks(application, result, ReferenceSnapshot(reference_version="unconfigured"))
    expected = result["pairs"] + result["unprocessed_pairs"]
    assert [(str(c.item_id), str(c.diagnosis_id)) for c in checks] == [
        (p["item_id"], p["diagnosis_id"]) for p in expected
    ]
    assert [c.number for c in checks] == [row["number"] for row in result["human_report"]["items"]]
    if mode == "partial":
        assert not checks[-1].processed and checks[-1].reasons == ["BUDGET_EXHAUSTED"]
    if mode == "fallback":
        assert all(c.generation_mode == "FALLBACK" for c in checks)
    assert result == before


@pytest.mark.parametrize("group", [None, "adult", "child"])
def test_save_keeps_group_and_read_works_without_ai(
    group: str | None, library_path: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import analysis

    application = make_application()
    application.age_group = group
    result = integration_result(application)
    session = Mock(spec=Session)
    session.scalar.return_value = application
    session.get.return_value = None
    monkeypatch.setattr(
        analysis, "_load_snapshot", lambda _: ReferenceSnapshot(reference_version="unconfigured")
    )
    stored = save_ai_result(
        session,
        application.id,
        result,
        currency="RUB",
        backend_revision="a" * 40,
        ai_revision="b" * 40,
    )
    assert all(
        diagnosis["conditions"]["age_group"] == group
        and diagnosis["conditions"]["age_years"] is None
        for diagnosis in stored.result["request"]["diagnoses"]
    )
    session.add.assert_called_once_with(stored)
    session.flush.assert_called_once()
    monkeypatch.setitem(sys.modules, "llm_rag", None)
    monkeypatch.setitem(sys.modules, "gigachat", None)
    assert read_ai_report(stored).human_report.model_dump(mode="json") == result["human_report"]


@pytest.mark.parametrize("group", [None, "adult", "child"])
def test_save_rejects_changed_age_group(group: str | None, library_path: None) -> None:
    application = make_application()
    application.age_group = group
    result = integration_result(application)
    application.age_group = "child" if group == "adult" else "adult"
    session = Mock(spec=Session)
    session.scalar.return_value = application
    with pytest.raises(AIReportError):
        save_ai_result(
            session,
            application.id,
            result,
            currency="RUB",
            backend_revision="a" * 40,
            ai_revision="b" * 40,
        )
    session.add.assert_not_called()
    session.flush.assert_not_called()


def test_http_errors_without_database() -> None:
    from app.api.applications import get_ai_report

    class Session:
        def __init__(self, present: bool) -> None:
            self.present = present

        def get(self, model: type, identifier: UUID) -> object:
            return make_application() if self.present and model is not ApplicationAIResult else None

    for present, status, code in ((False, 404, "not_found"), (True, 409, "report_not_ready")):
        response = get_ai_report(UUID(int=100), Session(present))
        assert response.status_code == status
        assert json.loads(response.body)["errors"][0]["code"] == code
