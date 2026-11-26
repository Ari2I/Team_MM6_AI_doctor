"""Ordinary input does not depend on synthetic names, purchases or a demo switch."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.config import AISettings
from app.models import Application, ApplicationDiagnosis, ApplicationItem
from app.schemas.analysis import PairCheck
from app.scripts.import_reference import ReferenceDocument
from app.services import analysis, standard_analysis
from app.services.ai_adapter import AIAdapter, AIInput
from tests.test_ai_analysis import MemorySession

pytestmark = pytest.mark.usefixtures("no_network")


def known_pair() -> AIInput:
    return AIInput(
        drug_name="Торговое название", mnn="Активное вещество", diagnosis="Диагноз из заявки"
    )


def known_rule() -> PairCheck:
    return PairCheck(category="А", source="Документ справочника", reference_version="reference-1")


def test_ordinary_offline_worker_needs_no_purchase_fields_or_demo_switch() -> None:
    service = AIAdapter(AISettings(mode="offline"))
    result = service.analyze(known_pair(), known_rule())
    assert result.category == "А" and result.source == "Документ справочника"
    assert result.reference_version == "reference-1" and not result.needs_review
    assert "Диагноз из заявки" in result.comment


def test_unknown_pair_never_calls_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("Unknown input must not trigger an ungrounded external classification")

    monkeypatch.setattr(standard_analysis, "create_provider", forbidden)
    result = standard_analysis.analyze_pair(
        known_pair().model_copy(update={"mnn": None}),
        PairCheck(needs_review=True, review_reason="DRUG_NOT_FOUND"),
        AISettings(mode="network"),
        5,
    )
    check = PairCheck.model_validate(result.check)
    assert check.category is None and check.needs_review
    assert check.source and check.reference_version and check.comment
    assert result.sent_fields == []


def test_missing_reference_returns_report_instead_of_application_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = Application(
        id=uuid4(),
        filename="input.xlsx",
        created_at=datetime.now(UTC),
        status="parsed",
        requires_review=False,
        diagnoses=[ApplicationDiagnosis(id=uuid4(), name="Произвольный диагноз")],
        items=[
            ApplicationItem(id=uuid4(), row_number=2, drug_name="Произвольный препарат", amount=100)
        ],
    )
    session = MemorySession(application)
    session.scalar = lambda _: None
    monkeypatch.setattr(analysis, "AIAdapter", lambda: AIAdapter(AISettings(mode="offline")))
    result = analysis.analyze_application(session, application.id)
    assert result.status == "analyzed" and result.traffic_light == "undetermined"
    assert result.requires_review and "не подключён" in result.warning_message
    assert result.category_shares["undetermined"] == 100
    assert len(session.checks) == 1
    check = session.checks[0]
    assert check.category is None and check.needs_review and check.source
    assert check.reference_version == "unconfigured"


@pytest.mark.parametrize("tamper", [False, True])
def test_network_receives_only_rule_and_pair(monkeypatch: pytest.MonkeyPatch, tamper: bool) -> None:
    state = SimpleNamespace(payload=None, closed=False)

    class Provider:
        usage_totals = {"total_tokens": 17}

        def generate(self, payload: dict[str, Any]) -> dict[str, Any]:
            state.payload = payload
            claim = {
                key: payload["evidence"][0][key]
                for key in ("claim_id", "text", "fragment_id", "quote")
            }
            if tamper:
                claim["text"] = "Придуманное основание"
            return {"item_id": "item", "claims": [claim]}

        def close(self) -> None:
            state.closed = True

    monkeypatch.setattr(standard_analysis, "create_provider", lambda _: Provider())
    pair = known_pair().model_copy(
        update={
            "drug_name": "private-trade",
            "dosage": "private-dose",
            "quantity": Decimal("3.5"),
            "amount": Decimal(999),
        }
    )
    result = standard_analysis.analyze_pair(pair, known_rule(), AISettings(mode="network"), 5)
    assert state.closed and result.tokens == 17
    assert "is_demo" not in state.payload
    assert "private" not in json.dumps(state.payload)
    assert set(state.payload["item"]) == {"item_id", "inn", "diagnosis", "category"}
    if tamper:
        assert result.error == "AI_INVALID_RESPONSE" and result.check is None
    else:
        assert PairCheck.model_validate(result.check).category == "А"


def test_real_reference_metadata_and_provenance_are_accepted() -> None:
    document = ReferenceDocument.model_validate(
        {
            "reference_version": "reference-1",
            "name": "Рабочий справочник",
            "is_synthetic": False,
            "drugs": [{"trade_name": "Торговое название", "mnn": "Активное вещество"}],
            "diagnoses": [{"diagnosis_id": "d1", "name": "Диагноз из заявки"}],
            "rules": [
                {
                    "mnn": "Активное вещество",
                    "diagnosis_id": "d1",
                    "category": "Б",
                    "source": "Документ справочника",
                }
            ],
        }
    )
    assert not document.is_synthetic and document.rules[0].category == "Б"
    missing_source = document.model_dump()
    missing_source["rules"][0]["source"] = None
    with pytest.raises(ValidationError, match="источник"):
        ReferenceDocument.model_validate(missing_source)
