"""Сборка защиты не читает SQL-справочник даже при отказе/частичном результате AI."""

import sys
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import AISettings
from app.models import ApplicationAIResult
from app.schemas.analysis import PairCheck
from app.services import ai_reports, analysis, application_ai
from app.services.ai_reports import read_ai_report
from app.services.integration_protocol import IntegrationReply
from app.services.rules import detect_conflicts
from tests.defense_data import defense_result
from tests.test_application_ai import IntegrationSession, make_application

pytestmark = pytest.mark.usefixtures("no_network")


@pytest.fixture
def defense_settings(monkeypatch):
    source = Path("ai/src").resolve()
    if not source.is_dir():
        pytest.skip("AI library is absent")
    monkeypatch.syspath_prepend(str(source))
    monkeypatch.setitem(sys.modules, "gigachat", None)
    config = AISettings(
        mode="network",
        use_backend_reference=False,
        backend_revision="a" * 40,
        library_revision="b" * 40,
    )
    monkeypatch.setattr(analysis, "AISettings", lambda: config)

    def forbidden(*args, **kwargs):
        pytest.fail("В сборке защиты вызван SQL-справочник или старый AI-адаптер.")

    for module, name in (
        (analysis, "AIAdapter"),
        (analysis, "_load_snapshot"),
        (analysis, "match_drug"),
        (analysis, "match_diagnosis"),
        (analysis, "check_pair"),
        (ai_reports, "match_drug"),
        (ai_reports, "match_diagnosis"),
        (ai_reports, "check_pair"),
    ):
        monkeypatch.setattr(module, name, forbidden)
    return config


@pytest.mark.parametrize("mode", ["template", "offline"])
def test_disabling_reference_cannot_enable_network_in_local_modes(mode):
    with pytest.raises(ValidationError):
        AISettings(mode=mode, use_backend_reference=False)
    assert AISettings(mode=mode).use_backend_reference


def test_diagnosis_conflict_keeps_verbatim_quote_at_end():
    quote = "Синтетическая дословная цитата: TEST_EVIDENCE."
    checks = detect_conflicts(
        [
            PairCheck(
                category="А",
                comment="Пояснение. Цитата: " + quote,
                needs_review=True,
                review_reason="AI_PRELIMINARY",
            ),
            PairCheck(category="Б"),
        ]
    )
    assert checks[0].comment.split(" Цитата: ")[1] == quote
    assert "Разные" in checks[0].comment or "разные" in checks[0].comment


@pytest.mark.parametrize(
    "mode,categories,first_reason",
    [
        ("evidence", ["Б", "Б", "Б"], "AI_PRELIMINARY"),
        ("approved", ["В", "Б", "Б"], "AI_REVIEW_REQUIRED"),
        ("none", [None, None, None], "AI_REVIEW_REQUIRED"),
        ("conflict", [None, "Б", "Б"], "AI_REVIEW_REQUIRED"),
        ("partial", [None, "Б", "Б"], "AI_TIMEOUT"),
        ("fallback", [None, None, None], "AI_UNAVAILABLE"),
        ("generation_error", ["Б", "Б", "Б"], "AI_UNAVAILABLE"),
    ],
)
@pytest.mark.parametrize("age_group", [None, "adult", "child"])
def test_one_facade_supplies_card_and_saved_report(
    mode, categories, first_reason, age_group, defense_settings, monkeypatch
):
    application = make_application()
    application.age_group = age_group
    session = IntegrationSession(application)
    calls = []
    expected = []

    def worker(self, prepared):
        calls.append(deepcopy(prepared.payload))
        value = defense_result(application, mode)
        expected.append(deepcopy(value))
        return IntegrationReply(request_id=application.id, outcome="result", result=value)

    monkeypatch.setattr(application_ai.IntegrationWorkerClient, "analyze", worker)
    result = analysis.analyze_application(session, application.id)
    assert result.status == "analyzed" and session.commits == 1
    assert len(calls) == 1 and len(calls[0]["purchases"]) == 2 and len(calls[0]["links"]) == 3
    # Ни МНН прежнего SQL-сопоставления, ни его confidence не переходят в новый вызов.
    assert all(not purchase.get("inn") for purchase in calls[0]["purchases"])
    assert all(d.get("conditions", {}).get("age_group") == age_group for d in calls[0]["diagnoses"])
    checks = sorted(session.checks, key=lambda c: (c.item_id, c.diagnosis_id))
    assert [c.category for c in checks] == categories
    assert checks[0].review_reason == first_reason
    assert all(c.needs_review and c.source.startswith("ИИ-анализ:") for c in checks)
    assert all("synthetic-sql" not in (c.reference_version or "") for c in checks)
    assert all("demo-api-2" not in (c.source or "") for c in checks)
    assert all(c.reference_version.startswith("grls:synthetic-defense-1") for c in checks)
    assert result.category_shares["total_amount"] == 1000
    assert result.requires_review and all(item.match_confidence is None for item in result.items)
    assert session.report.result == expected[0]
    assert expected[0]["human_report"]["summary"] in result.summary
    assert expected[0]["human_report"]["recommendation_note"] in result.summary
    assert all(c["backend_rule"]["category"] is None for c in session.report.checks)
    assert all(c["backend_rule"]["reference_version"] is None for c in session.report.checks)
    assert all("отключена" in c["backend_rule"]["source"] for c in session.report.checks)
    if mode not in {"partial", "fallback"}:
        first_pair = expected[0]["pairs"][0]
        assert checks[0].comment.endswith(" Цитата: " + first_pair["citations"][0]["quote"])
        assert first_pair["citations"][0]["url"] in checks[0].source
        assert application.items[0].mnn == first_pair["inn"]
    if mode in {"none", "fallback"}:
        assert result.category_shares["undetermined"] == 100
    if mode == "evidence":
        assert result.category_shares["Б"] == 100
    monkeypatch.setitem(sys.modules, "llm_rag", None)
    monkeypatch.setitem(sys.modules, "gigachat", None)
    assert read_ai_report(session.report).human_report.model_dump() == expected[0]["human_report"]


@pytest.mark.parametrize(
    "reason", ["AI_TIMEOUT", "AI_UNAVAILABLE", "AI_INVALID_RESPONSE", "AI_INPUT_INCOMPLETE"]
)
def test_worker_failure_clears_previous_categories_mnn_and_report(
    reason, defense_settings, monkeypatch
):
    application = make_application()
    session = IntegrationSession(application)
    monkeypatch.setattr(
        application_ai.IntegrationWorkerClient,
        "analyze",
        lambda self, prepared: IntegrationReply(
            request_id=application.id, outcome="result", result=defense_result(application)
        ),
    )
    assert analysis.analyze_application(session, application.id).status == "analyzed"
    assert session.report is not None and session.checks[0].category == "Б"
    monkeypatch.setattr(
        application_ai.IntegrationWorkerClient,
        "analyze",
        lambda self, prepared: IntegrationReply(
            request_id=application.id, outcome="error", error=reason
        ),
    )
    result = analysis.analyze_application(session, application.id)
    assert result.status == "analyzed" and result.requires_review
    assert session.report is None
    assert all(c.category is None and c.review_reason == reason for c in session.checks)
    assert all(c.reference_version is None for c in session.checks)
    assert all(item.mnn is None and item.match_confidence is None for item in result.items)
    assert result.category_shares["undetermined"] == 100


def test_invalid_result_never_falls_back_to_sql(defense_settings, monkeypatch):
    application = make_application()
    session = IntegrationSession(application)
    session.report = ApplicationAIResult(application_id=application.id)

    def worker(self, prepared):
        value = defense_result(application)
        value["request"]["purchases"][0]["name"] = "Другая синтетическая позиция"
        return IntegrationReply(request_id=application.id, outcome="result", result=value)

    monkeypatch.setattr(application_ai.IntegrationWorkerClient, "analyze", worker)
    result = analysis.analyze_application(session, application.id)
    assert result.status == "analyzed" and session.report is None
    assert all(
        c.category is None and c.review_reason == "AI_INVALID_RESPONSE" for c in session.checks
    )


def test_projection_links_reordered_pairs_and_explanations_by_ids(defense_settings, monkeypatch):
    from llm_rag.integration_contracts import IntegrationResult

    application = make_application()
    session = IntegrationSession(application)

    def worker(self, prepared):
        value = defense_result(application)
        value["pairs"].reverse()
        value["pairs"][0]["decision"] = {
            "upstream_rule_category": "В",
            "classification_basis": "APPROVED_RULE",
        }
        value["human_report"] = {}
        value = IntegrationResult.model_validate(value).model_dump(mode="json")
        return IntegrationReply(request_id=application.id, outcome="result", result=value)

    monkeypatch.setattr(application_ai.IntegrationWorkerClient, "analyze", worker)
    assert analysis.analyze_application(session, application.id).status == "analyzed"
    checks = sorted(session.checks, key=lambda c: (c.item_id, c.diagnosis_id))
    assert [c.category for c in checks] == ["Б", "Б", "В"]
    assert " Цитата: " in checks[0].comment and "example.org" in checks[0].source
    assert all(" Цитата: " not in c.comment for c in checks[1:])


@pytest.mark.parametrize("missing", ["backend_revision", "library_revision"])
def test_missing_build_metadata_never_calls_facade(defense_settings, monkeypatch, missing):
    config = defense_settings.model_copy(update={missing: ""})
    monkeypatch.setattr(analysis, "AISettings", lambda: config)
    monkeypatch.setattr(
        application_ai.IntegrationWorkerClient,
        "analyze",
        lambda *a: pytest.fail("Фасад вызван без SHA сборки."),
    )
    session = IntegrationSession(make_application())
    result = analysis.analyze_application(session, session.application.id)
    assert result.status == "analyzed" and session.report is None
    assert all(c.category is None and c.review_reason == "AI_UNAVAILABLE" for c in session.checks)
