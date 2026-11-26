"""Фасад работает с неполными покупками и не создаёт скрытые пары."""

from datetime import date
import pytest
from llm_rag.integration_contracts import IntegrationRequest
from llm_rag.ml2.registry import RegistrySnapshot


def request(count=2):
    return IntegrationRequest.model_validate(
        dict(
            schema_version="integration-request.1",
            request_id="R",
            diagnoses=[dict(diagnosis_id=f"D{i}", name=f"Диагноз {i}") for i in range(count)],
            purchases=[dict(item_id="P", name="Неизвестный препарат", amount="100.10", currency="RUB")],
            links=[dict(item_id="P", diagnosis_id=f"D{i}") for i in range(count)],
        )
    )


@pytest.mark.parametrize("count", [1, 2, 10, 50])
def test_offline_facade_preserves_pairs_and_counts_money_once(count, monkeypatch):
    from llm_rag.integration import _run, IntegrationResources
    import socket

    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("Офлайн-фасад открыл сеть"))
    registry = RegistrySnapshot(version="empty", as_of=date(2026, 11, 17), registrations=())
    result = _run(request(count), resources=IntegrationResources(registry=registry))
    assert len(result.pairs) == count
    assert result.completeness == "COMPLETE"
    assert result.usage.calls == 0
    assert str(result.costs.totals["RUB"]) == "100.10"
    assert all(p.inn is None and p.decision.proposed_category is None for p in result.pairs)
    assert len(result.human_report["items"]) == count
    assert "is_demo" not in result.request.model_dump()


def test_budget_reports_exact_unprocessed_pairs():
    from llm_rag.integration import _run, IntegrationResources, IntegrationOptions

    result = _run(
        request(10), resources=IntegrationResources(), options=IntegrationOptions(max_pairs=2)
    )
    assert len(result.pairs) == 2
    assert len(result.unprocessed_pairs) == 8
    assert result.completeness == "PARTIAL"
    assert all(p.reason == "BUDGET_EXHAUSTED" for p in result.unprocessed_pairs)


def test_missing_registry_is_explicit_source_error():
    from llm_rag.integration import _run, IntegrationResources

    result = _run(request(), resources=IntegrationResources())
    assert all("SOURCE_ERROR" in p.reasons for p in result.pairs)
    assert all(p.evidence_status == "PROCESSING_ERROR" for p in result.pairs)


def test_pinned_version_mismatch_prevents_processing():
    from llm_rag.integration import _run, IntegrationResources

    value = request()
    value.versions.registry = "missing"
    result = _run(value, resources=IntegrationResources())
    assert not result.pairs
    assert all(p.reason == "VERSION_MISMATCH" for p in result.unprocessed_pairs)


def test_registered_drug_reaches_evidence_path():
    from llm_rag.integration import _run, IntegrationResources
    from llm_rag.ml2.registry import RegistryEntry, RegistrySource

    source = RegistrySource(
        title="Реестр",
        url="https://grls.rosminzdrav.ru/GRLS.aspx",
        version="r1",
        location="строка 1",
        quote="Вещество",
        retrieved_at=date(2026, 11, 17),
    )
    registry = RegistrySnapshot(
        version="r1",
        as_of=date(2026, 11, 17),
        registrations=(
            RegistryEntry(
                registration="1",
                trade_name="Неизвестный препарат",
                mnn="Вещество",
                form="50 мг",
                source=source,
            ),
        ),
    )
    result = _run(request(), resources=IntegrationResources(registry=registry))
    assert all(p.inn == "Вещество" and p.reasons == ["NO_EVIDENCE"] for p in result.pairs)
    assert all(p.explanation is not None for p in result.pairs)
    value = request()
    value.purchases[0].dosage = "100 мг"
    mismatch = _run(value, resources=IntegrationResources(registry=registry))
    assert all(p.form_status == "MISMATCH" and p.inn == "Вещество" for p in mismatch.pairs)
    assert all("концентрация не совпала" in row["comment"] for row in mismatch.human_report["items"])


def test_one_pair_failure_does_not_break_other_pairs(monkeypatch):
    from llm_rag.integration import _run, IntegrationResources
    from llm_rag.ml2.pipeline import ML2Pipeline

    original = ML2Pipeline.evaluate_pair

    def evaluate(self, **kwargs):
        if kwargs["diagnosis"] == "Диагноз 0":
            raise OSError("Ошибка чтения")
        return original(self, **kwargs)

    monkeypatch.setattr(ML2Pipeline, "evaluate_pair", evaluate)
    registry = RegistrySnapshot(version="empty", as_of=date(2026, 11, 17), registrations=())
    result = _run(request(), resources=IntegrationResources(registry=registry))
    assert result.pairs[0].generation_mode == "FALLBACK"
    assert result.pairs[1].reasons == ["INN_UNRESOLVED"]


def test_deadline_is_checked_before_every_pair():
    from llm_rag.integration import _run, IntegrationResources, IntegrationOptions

    values = iter([0.0, 0.0, 2.0, 2.0])
    result = _run(
        request(),
        resources=IntegrationResources(clock=lambda: next(values)),
        options=IntegrationOptions(deadline_seconds=1.0),
    )
    assert len(result.pairs) == 1 and len(result.unprocessed_pairs) == 1


@pytest.mark.parametrize("value", [True, False])
def test_known_pregnancy_is_used_only_in_local_context(value, monkeypatch):
    from llm_rag.integration import _run, IntegrationResources
    from llm_rag.ml2.pipeline import ML2Pipeline

    captured = []
    original = ML2Pipeline.evaluate_pair

    def evaluate(self, **kwargs):
        captured.append(kwargs["context"])
        return original(self, **kwargs)

    monkeypatch.setattr(ML2Pipeline, "evaluate_pair", evaluate)
    data = request(1)
    data.diagnoses[0].conditions.pregnant = value
    registry = RegistrySnapshot(version="empty", as_of=date(2026, 11, 17), registrations=())
    _run(data, resources=IntegrationResources(registry=registry))
    assert captured[0]["pregnant"] == str(value).lower()


def test_known_amount_with_missing_currency_is_visible():
    from llm_rag.integration import _run, IntegrationResources
    from llm_rag.human_report import render_human_report

    data = request(1)
    data.purchases[0].currency = None
    result = _run(data, resources=IntegrationResources())
    html = render_human_report(result.human_report)
    assert "100.10" in html and "валюта не указана" in html.lower()


def test_current_policy_can_be_pinned_and_has_manifest_sha():
    from llm_rag.integration import _run, IntegrationResources
    from llm_rag.ml2.policy import load_classification_rules

    value = request(1)
    value.versions.policy = load_classification_rules().policy_version
    result = _run(value, resources=IntegrationResources())
    assert not result.unprocessed_pairs
    assert result.manifest.policy.version == value.versions.policy
    assert len(result.manifest.policy.sha256) == 64


def test_manifest_describes_loaded_classifier_policy_not_changed_file(monkeypatch):
    from datetime import date
    from llm_rag.integration import _run, IntegrationResources
    from llm_rag.ml2 import policy

    active = policy.load_classification_rules()
    monkeypatch.setattr(
        policy,
        "load_classification_rules",
        lambda: active.model_copy(update={"policy_version": "unloaded-file-update"}),
    )
    registry = RegistrySnapshot(version="empty", as_of=date(2026, 11, 17), registrations=())
    result = _run(request(1), resources=IntegrationResources(registry=registry))
    assert result.manifest.policy.version == result.pairs[0].policy_version
