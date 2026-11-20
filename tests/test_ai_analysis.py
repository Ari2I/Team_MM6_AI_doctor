"""Оркестрация нескольких пар и PostgreSQL roundtrip новых кодов причин."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.api.applications import _card
from app.config import REVIEW_REASON_LABELS, AISettings
from app.models import Application, ApplicationDiagnosis, ApplicationItem, ItemCheck
from app.models.enums import ReviewReason
from app.schemas.analysis import PairCheck, ReferenceSnapshot
from app.scripts.import_reference import read_reference
from app.services import analysis
from app.services.ai_adapter import AIAdapter
from app.services.excel_parser import parse_excel
from app.services.rules import detect_conflicts

pytest_plugins = ["tests.test_demo_v2"]


class MemorySession:
    """Подмена только хранения; парсер, matching, правила и анализ настоящие."""

    def __init__(self, application: Application) -> None:
        self.application = application
        self.checks: list[ItemCheck] = []

    def get(self, model: Any, identifier: UUID | str) -> Application:
        return self.application

    def execute(self, statement: Any) -> None:
        pass

    def flush(self) -> None:
        pass

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pytest.fail("AI error rolled back entire application")

    def add(self, check: ItemCheck) -> None:
        check.item = next(item for item in self.application.items if item.id == check.item_id)
        check.diagnosis = next(d for d in self.application.diagnoses if d.id == check.diagnosis_id)
        self.checks.append(check)


def demo_application() -> tuple[Application, ReferenceSnapshot]:
    parsed = parse_excel(Path("data/demo/correct.xlsx"))
    document = read_reference(Path("data/reference/demo-v2.json"))
    snapshot = ReferenceSnapshot.model_validate(document.model_dump())
    names = list(dict.fromkeys(item.diagnosis for item in parsed.items if item.diagnosis))
    application = Application(
        id=uuid4(),
        filename="correct.xlsx",
        created_at=datetime.now(UTC),
        status="parsed",
        requires_review=False,
        diagnoses=[ApplicationDiagnosis(id=uuid4(), name=name) for name in names],
        items=[ApplicationItem(id=uuid4(), **item.model_dump()) for item in parsed.items],
    )
    return application, snapshot


def test_pair_failure_does_not_fail_application(monkeypatch: pytest.MonkeyPatch) -> None:
    application, snapshot = demo_application()
    session = MemorySession(application)
    monkeypatch.setattr(analysis, "_load_snapshot", lambda _: snapshot)
    service = AIAdapter(AISettings(mode="offline"))
    monkeypatch.setattr(analysis, "AIAdapter", lambda: service)
    calls = []

    def pair_check(pair: Any, local: PairCheck) -> PairCheck:
        calls.append(pair)
        if len(calls) == 1:
            return service._fallback(local, ReviewReason.AI_TIMEOUT)
        return local

    monkeypatch.setattr(service, "analyze", pair_check)
    result = analysis.analyze_application(session, application.id)
    assert result.status == "analyzed" and result.requires_review
    assert len(calls) == 3 and len(session.checks) == 3
    assert session.checks[0].review_reason == "AI_TIMEOUT"
    assert [c.category for c in session.checks] == ["А", "Б", "В"]
    assert result.category_shares["total_amount"] == 1000
    assert REVIEW_REASON_LABELS["AI_TIMEOUT"].rstrip(".") in result.warning_message
    assert "AI_TIMEOUT" not in result.warning_message
    assert _card(application).application.status == "analyzed"


def test_conflict_preserves_ai_failure_code() -> None:
    checks = detect_conflicts(
        [
            PairCheck(
                category="А", needs_review=True, review_reason="AI_TIMEOUT", comment="Шаблон"
            ),
            PairCheck(category="Б"),
        ]
    )
    assert checks[0].review_reason == "AI_TIMEOUT"
    assert checks[1].review_reason == "CONFLICTING_CATEGORIES"
    assert all(check.needs_review for check in checks)


def test_real_offline_application(monkeypatch: pytest.MonkeyPatch) -> None:
    if not Path("ai/src/llm_rag/pipeline.py").is_file():
        pytest.skip("В template-образе AI намеренно отсутствует")
    application, snapshot = demo_application()
    session = MemorySession(application)
    monkeypatch.setattr(analysis, "_load_snapshot", lambda _: snapshot)
    monkeypatch.setattr(
        analysis,
        "AIAdapter",
        lambda: AIAdapter(
            AISettings(mode="offline", corpus_path="ai/examples/corpus.json"),
        ),
    )
    result = analysis.analyze_application(session, application.id)
    assert result.status == "analyzed" and result.traffic_light == "green"
    assert not result.requires_review
    card = _card(result)
    assert len(card.items) == 3 and all(i.checks[0].comment for i in card.items)
    assert all(i.checks[0].reference_version == "demo-api-2" for i in card.items)
    assert card.disclaimer


@pytest.mark.skipif(os.environ.get("RUN_POSTGRES_TESTS") != "1", reason="Нужен PostgreSQL")
@pytest.mark.parametrize(
    "reason", [reason for reason in ReviewReason if reason.value.startswith("AI_")]
)
def test_ai_reason_roundtrip_postgres(reason: ReviewReason) -> None:
    from sqlalchemy.orm import Session

    from app.db.session import engine

    application, _ = demo_application()
    with engine.connect() as connection, connection.begin() as transaction:
        with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
            check = ItemCheck(
                item=application.items[0],
                diagnosis=application.diagnoses[0],
                needs_review=True,
                review_reason=reason.value,
            )
            session.add(application)
            session.flush()
            identifier = check.id
            session.commit()
            session.expire_all()
            assert session.get(ItemCheck, identifier).review_reason == reason.value
        transaction.rollback()


@pytest.mark.parametrize("reason", ["AI_UNAVAILABLE", "AI_TIMEOUT", "AI_INVALID_RESPONSE"])
def test_postgres_card_survives_optional_ai_failure(
    demo_client: Any, monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    calls = []

    def analyze(self: AIAdapter, pair: Any, local: PairCheck) -> PairCheck:
        calls.append(pair)
        return self._fallback(local, ReviewReason(reason)) if len(calls) == 1 else local

    monkeypatch.setattr(AIAdapter, "analyze", analyze)
    response = demo_client.post(
        "/applications",
        files={
            "file": ("correct.xlsx", Path("data/demo/correct.xlsx").read_bytes()),
        },
    )
    assert response.status_code == 201
    card = response.json()
    assert card["application"]["status"] == "analyzed"
    assert card["items"][0]["checks"][0]["review_reason"] == reason
    assert all(not item["checks"][0]["needs_review"] for item in card["items"][1:])
    assert len(calls) == 3
    assert demo_client.get(f"/applications/{card['application']['id']}").json() == card
