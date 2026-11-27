"""Загрузка → один worker → снимок/карточка; только синтетические данные, без сети."""

import sys
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.config import AISettings
from app.models import ApplicationAIResult
from app.schemas.analysis import PairCheck, ReferenceSnapshot
from app.services import analysis, application_ai
from app.services.ai_adapter import AIAdapter
from app.services.ai_reports import AIReportError, read_ai_report
from app.services.application_ai import ApplicationAI, project_pair
from app.services.integration_protocol import IntegrationReply
from tests.ai_report_data import integration_result
from tests.test_ai_analysis import MemorySession
from tests.test_integration_mapping import make_application as mapped_application

pytestmark = pytest.mark.usefixtures("no_network")


def make_application():
    application = mapped_application()
    # Название должно отличаться и при нечётком поиске: в этих тестах вторая
    # позиция намеренно не имеет SQL-правила.
    application.items[1].drug_name = "Иная тестовая позиция"
    return application


@pytest.fixture
def library_path(monkeypatch: pytest.MonkeyPatch) -> None:
    source = Path("ai/src").resolve()
    if not source.is_dir():
        pytest.skip("AI library is absent")
    monkeypatch.syspath_prepend(str(source))
    monkeypatch.setitem(sys.modules, "gigachat", None)


def settings(**kwargs) -> AISettings:
    return AISettings(
        mode="network", backend_revision="a" * 40, library_revision="b" * 40, **kwargs
    )


def snapshot() -> ReferenceSnapshot:
    return ReferenceSnapshot(
        reference_version="synthetic-sql-1",
        drugs=[dict(trade_name="Синтетический препарат 1", mnn="Синтетическое вещество")],
        diagnoses=[dict(diagnosis_id="d", name="J45.9 Синтетический диагноз 1")],
        rules=[dict(mnn="Синтетическое вещество", diagnosis_id="d", category="А", source="SQL")],
    )


class IntegrationSession(MemorySession):
    """Подмена хранилища, при этом настоящий save_ai_result валидирует вход/снимок."""

    def __init__(self, application):
        super().__init__(application)
        self.report = None
        self.commits = 0
        self.savepoint_rollbacks = 0

    def get(self, model, identifier):
        return self.report if model is ApplicationAIResult else self.application

    def scalar(self, statement):
        return self.application

    def execute(self, statement):
        table = getattr(statement, "table", None)
        if table is not None and table.name == "application_ai_results":
            self.report = None
        elif table is not None and table.name == "item_checks":
            self.checks.clear()

    def add(self, value):
        if isinstance(value, ApplicationAIResult):
            self.report = value
        else:
            super().add(value)

    def commit(self):
        self.commits += 1

    @contextmanager
    def begin_nested(self):
        original = self.report
        try:
            yield
        except Exception:
            self.report = original
            self.savepoint_rollbacks += 1
            raise


def configure(monkeypatch, session, config=None):
    config = config or settings()
    monkeypatch.setattr(analysis, "AIAdapter", lambda: AIAdapter(config))
    reference = snapshot()
    monkeypatch.setattr(analysis, "_load_snapshot", lambda _: reference)
    return reference


@pytest.mark.parametrize("mode", ["complete", "partial", "fallback", "evidence"])
@pytest.mark.parametrize("group", [None, "adult", "child"])
def test_one_worker_saves_full_result_and_card(mode, group, library_path, monkeypatch) -> None:
    application = make_application()
    application.age_group = group
    session = IntegrationSession(application)
    configure(monkeypatch, session)
    calls = []

    def worker(self, prepared):
        calls.append(prepared)
        return IntegrationReply(
            request_id=application.id,
            outcome="result",
            result=integration_result(application, mode),
        )

    monkeypatch.setattr(application_ai.IntegrationWorkerClient, "analyze", worker)
    monkeypatch.setattr(AIAdapter, "analyze", lambda *a: pytest.fail("Запущен попарный network"))
    result = analysis.analyze_application(session, application.id)
    assert result.status == "analyzed" and session.commits == 1
    assert len(calls) == 1 and len(session.checks) == 3
    assert all(
        d.get("conditions", {}).get("age_group") == group for d in calls[0].payload["diagnoses"]
    )
    assert result.category_shares["total_amount"] == 1000
    assert all(check.needs_review for check in session.checks)
    sql = next(c for c in session.checks if c.item_id == UUID(int=11))
    assert sql.category == "А" and sql.source == "SQL"
    assert sql.reference_version == "synthetic-sql-1"
    assert session.report.backend_revision == "a" * 40
    assert session.report.ai_revision == "b" * 40
    assert all(
        c["backend_rule"]["reference_version"] == "synthetic-sql-1" for c in session.report.checks
    )
    if mode == "partial":
        assert session.report.result["completeness"] == "PARTIAL"
        assert sql.review_reason == "AI_TIMEOUT"
    if mode == "fallback":
        assert all(c.review_reason == "AI_UNAVAILABLE" for c in session.checks)
    if mode == "complete":
        assert "концентрации" in sql.comment
        assert session.report.checks[0]["conflict"]
        proposals = [c for c in session.checks if c.item_id == UUID(int=12)]
        assert all(c.category == "Б" and c.review_reason == "AI_PRELIMINARY" for c in proposals)
    monkeypatch.setitem(sys.modules, "llm_rag", None)
    monkeypatch.setitem(sys.modules, "gigachat", None)
    monkeypatch.setattr(
        application_ai.IntegrationWorkerClient, "analyze", lambda *a: pytest.fail("Повтор сети")
    )
    assert read_ai_report(session.report) == read_ai_report(session.report)


@pytest.mark.parametrize(
    "reason", ["AI_TIMEOUT", "AI_UNAVAILABLE", "AI_INVALID_RESPONSE", "AI_INPUT_INCOMPLETE"]
)
def test_worker_error_keeps_sql_card_and_removes_old_report(reason, monkeypatch):
    application = make_application()
    session = IntegrationSession(application)
    session.report = ApplicationAIResult(application_id=application.id)
    configure(monkeypatch, session)
    monkeypatch.setattr(
        application_ai.IntegrationWorkerClient,
        "analyze",
        lambda self, prepared: IntegrationReply(
            request_id=application.id, outcome="error", error=reason
        ),
    )
    result = analysis.analyze_application(session, application.id)
    assert result.status == "analyzed" and result.requires_review
    assert session.report is None and session.commits == 1
    assert all(c.review_reason == reason for c in session.checks)
    assert session.checks[0].category == "А"
    assert result.category_shares["total_amount"] == 1000


@pytest.mark.parametrize("mode", ["template", "offline"])
def test_local_analysis_never_calls_new_worker_or_saves_report(mode, monkeypatch):
    application = make_application()
    session = IntegrationSession(application)
    session.report = ApplicationAIResult(application_id=application.id)
    configure(monkeypatch, session, AISettings(mode=mode))
    monkeypatch.setitem(sys.modules, "llm_rag", None)
    monkeypatch.setitem(sys.modules, "gigachat", None)
    monkeypatch.setattr(AIAdapter, "analyze", lambda self, pair, local: local)
    monkeypatch.setattr(
        analysis, "analyze_network_application", lambda *a: pytest.fail("Новый worker")
    )
    result = analysis.analyze_application(session, application.id)
    assert result.status == "analyzed" and session.report is None
    assert session.checks[0].category == "А" and not session.checks[0].needs_review


@pytest.mark.parametrize("missing", ["backend_revision", "library_revision"])
def test_missing_revision_prevents_worker(missing, monkeypatch):
    application = make_application()
    session = IntegrationSession(application)
    config = settings().model_copy(update={missing: ""})
    configure(monkeypatch, session, config)
    monkeypatch.setattr(
        application_ai.IntegrationWorkerClient, "analyze", lambda *a: pytest.fail("Нет SHA")
    )
    result = analysis.analyze_application(session, application.id)
    assert result.status == "analyzed" and session.report is None
    assert all(c.review_reason == "AI_UNAVAILABLE" for c in session.checks)


@pytest.mark.parametrize("field", ["backend_revision", "library_revision"])
def test_revision_must_be_full_sha(field):
    with pytest.raises(ValidationError):
        AISettings(**{field: "8836cdb"})


@pytest.mark.parametrize("damage", ["invalid_result", "save_error", "worker_crash", "skipped"])
def test_invalid_or_unsaved_result_cannot_affect_card(
    damage, library_path, monkeypatch, caplog, capsys
):
    application = make_application()
    session = IntegrationSession(application)
    configure(monkeypatch, session)
    marker = "PRIVATE-WORKER-MARKER"

    def worker(self, prepared):
        if damage == "worker_crash":
            raise RuntimeError(marker)
        if damage == "skipped":
            return IntegrationReply(request_id=application.id, outcome="skipped")
        value = integration_result(application)
        if damage == "invalid_result":
            value["human_report"]["summary"] = marker
        return IntegrationReply(request_id=application.id, outcome="result", result=value)

    monkeypatch.setattr(application_ai.IntegrationWorkerClient, "analyze", worker)
    if damage == "save_error":

        def broken_save(*args, **kwargs):
            session.report = ApplicationAIResult(application_id=application.id)
            raise AIReportError()

        monkeypatch.setattr(application_ai, "save_ai_result", broken_save)
    result = analysis.analyze_application(session, application.id)
    assert result.status == "analyzed" and session.report is None
    assert session.checks[0].category == "А"
    assert all(c.category is None for c in session.checks[1:])
    expected = "AI_UNAVAILABLE" if damage == "worker_crash" else "AI_INVALID_RESPONSE"
    assert all(c.review_reason == expected for c in session.checks)
    assert marker not in caplog.text and marker not in str(capsys.readouterr())
    assert marker not in result.summary and marker not in result.warning_message
    if damage in {"invalid_result", "save_error"}:
        assert session.savepoint_rollbacks == 1


def test_projection_conflict_keeps_sql_and_separates_form(library_path):
    result = integration_result(make_application())
    pair = result["pairs"][0]
    value = ApplicationAI(pairs={(pair["item_id"], pair["diagnosis_id"]): pair})
    local = PairCheck(category="А", source="SQL", reference_version="sql-v1")
    projected = project_pair(local, value, pair["item_id"], pair["diagnosis_id"])
    assert projected.category == local.category and projected.source == local.source
    assert projected.reference_version == local.reference_version
    assert projected.needs_review and projected.review_reason == "AI_REVIEW_REQUIRED"
    assert "расходится" in projected.comment and "концентрации" in projected.comment
    assert local.comment is None and not local.needs_review
    before = deepcopy(projected)
    pair["form_status"] = "MATCH"
    without_warning = project_pair(local, value, pair["item_id"], pair["diagnosis_id"])
    assert without_warning.category == before.category
    assert "концентрации" not in without_warning.comment


def test_library_conflict_never_selects_best_category(library_path):
    pair = integration_result(make_application())["pairs"][0]
    pair["decision"].update(upstream_rule_category="А", classification_basis="CONFLICT")
    outcome = ApplicationAI(pairs={(pair["item_id"], pair["diagnosis_id"]): pair})
    value = project_pair(PairCheck(), outcome, pair["item_id"], pair["diagnosis_id"])
    assert value.category is None and value.needs_review
    assert value.review_reason == "AI_REVIEW_REQUIRED"


def test_missing_pair_is_safe_fallback():
    value = project_pair(PairCheck(category="А"), ApplicationAI(), "item", "diagnosis")
    assert value.category == "А" and value.review_reason == "AI_INVALID_RESPONSE"


@pytest.mark.parametrize("conflicting", [False, True])
def test_inn_from_worker_does_not_override_sql_or_invent_confidence(
    conflicting, library_path, monkeypatch
):
    application = make_application()
    session = IntegrationSession(application)
    configure(monkeypatch, session)

    def worker(self, prepared):
        result = integration_result(application)
        for index, pair in enumerate(result["pairs"]):
            pair["inn"] = (
                "Другое синтетическое вещество"
                if conflicting and index == 2
                else "Синтетическое МНН фасада"
            )
        result["human_report"] = {}
        return IntegrationReply(request_id=application.id, outcome="result", result=result)

    monkeypatch.setattr(application_ai.IntegrationWorkerClient, "analyze", worker)
    result = analysis.analyze_application(session, application.id)
    assert result.status == "analyzed" and session.report is not None
    assert application.items[0].mnn == "Синтетическое вещество"
    if conflicting:
        assert application.items[1].mnn is None
    else:
        assert application.items[1].mnn == "Синтетическое МНН фасада"
        assert application.items[1].match_confidence is None
