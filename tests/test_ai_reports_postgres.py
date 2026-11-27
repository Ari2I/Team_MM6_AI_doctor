"""Реальные миграция, транзакции, JSONB и HTTP; только синтетические результаты AI."""

import importlib.util
import json
import os
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text, update
from sqlalchemy.orm import Session

from app.api.applications import get_session
from app.main import app
from app.models import Application, ApplicationAIResult, ItemCheck, ReferenceRule, ReferenceVersion
from app.scripts.import_reference import ReferenceDocument, import_reference
from app.services.ai_reports import AIReportError, read_ai_report, save_ai_result
from tests.ai_report_data import integration_result
from tests.test_integration_mapping import make_application

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_POSTGRES_TESTS") != "1", reason="Нужен PostgreSQL и RUN_POSTGRES_TESTS=1"
)


@pytest.fixture
def session():
    from app.db.session import engine

    with engine.connect() as connection, connection.begin() as transaction:
        with Session(
            bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
        ) as session:
            yield session
        transaction.rollback()


@pytest.fixture
def library_path(monkeypatch: pytest.MonkeyPatch) -> None:
    source = Path("ai/src").resolve()
    if not source.is_dir():
        pytest.skip("AI library is absent")
    monkeypatch.syspath_prepend(str(source))


@pytest.fixture
def application(session: Session) -> Application:
    application = make_application()
    application.filename = "synthetic.xlsx"
    application.status = "parsed"
    session.add(application)
    session.flush()
    document = ReferenceDocument.model_validate(
        dict(
            reference_version="ai-report-synthetic-1",
            name="Синтетический справочник",
            description="Не клинические рекомендации.",
            is_synthetic=True,
            drugs=[dict(trade_name=application.items[0].drug_name, mnn=application.items[0].mnn)],
            diagnoses=[dict(diagnosis_id="d", name=application.diagnoses[0].name)],
            rules=[
                dict(
                    mnn=application.items[0].mnn,
                    diagnosis_id="d",
                    category="А",
                    source="Синтетическое backend-правило",
                )
            ],
        )
    )
    import_reference(session, document, activate=True)
    session.add(
        ItemCheck(
            item_id=application.items[0].id,
            diagnosis_id=application.diagnoses[0].id,
            category="В",
            source="Синтетическое прежнее AI-предложение",
        )
    )
    session.flush()
    return application


def save(session: Session, application: Application, mode: str = "complete") -> ApplicationAIResult:
    return save_ai_result(
        session,
        application.id,
        integration_result(application, mode),
        currency="RUB",
        backend_revision="a" * 40,
        ai_revision="b" * 40,
    )


def test_save_accepts_database_money_scale_without_changing_request(
    session: Session, application: Application, library_path: None
) -> None:
    item = next(row for row in application.items if row.row_number == 2)
    item.amount = Decimal("900")
    session.flush()
    result = integration_result(application)
    sent_amount = next(
        row["amount"] for row in result["request"]["purchases"] if row["item_id"] == str(item.id)
    )
    assert sent_amount == "900"
    stored = save_ai_result(
        session,
        application.id,
        result,
        currency="RUB",
        backend_revision="a" * 40,
        ai_revision="b" * 40,
    )
    session.refresh(item)
    assert item.amount.as_tuple().exponent == -2
    assert stored.result["request"] == result["request"]


@pytest.mark.parametrize("mode", ["complete", "partial", "fallback", "evidence"])
@pytest.mark.parametrize("group", [None, "adult", "child"])
def test_jsonb_and_http_roundtrip(
    session: Session,
    application: Application,
    library_path: None,
    mode: str,
    group: str | None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    application.age_group = group
    session.flush()
    stored = save(session, application, mode)
    expected = deepcopy(stored.result)
    session.commit()
    session.expire_all()
    loaded = session.get(ApplicationAIResult, application.id)
    assert loaded.result == expected
    assert all(
        diagnosis["conditions"]["age_group"] == group
        and diagnosis["conditions"]["age_years"] is None
        for diagnosis in loaded.result["request"]["diagnoses"]
    )
    report = read_ai_report(loaded)
    assert report.human_report.model_dump(mode="json") == expected["human_report"]
    if mode == "evidence":
        assert loaded.result["evidence_snapshot"]["documents"]
        assert (
            report.human_report.items[0].sources[0].quote
            == expected["pairs"][0]["citations"][0]["quote"]
        )
    check = next(c for c in report.checks if c.item_id == application.items[0].id)
    assert check.backend_rule.category == "А"
    assert check.backend_rule.reference_version == "ai-report-synthetic-1"
    if mode == "complete":
        assert check.ai_proposed_category == "Б" and check.conflict
        assert check.form_status == "MISMATCH"
    assert (
        session.scalar(
            select(ItemCheck).where(
                ItemCheck.item_id == application.items[0].id,
                ItemCheck.diagnosis_id == application.diagnoses[0].id,
            )
        ).category
        == "В"
    )
    assert application.status.value == "parsed"

    # Переключение справочника не изменяет сохранённый снимок SQL-оснований.
    session.execute(update(ReferenceVersion).values(is_active=False))
    session.execute(update(ReferenceRule).values(category="В"))
    session.flush()
    from app.api import applications
    from tests.conftest import no_network

    original_read = applications.read_ai_report

    def guarded_read(stored: ApplicationAIResult):
        # HTTP и БД имеют собственные соединения. Чистое чтение результата
        # внутри обработчика не должно создавать даже служебный сокет.
        with pytest.MonkeyPatch.context() as patch:
            guard = no_network.__wrapped__(patch, tmp_path)
            next(guard)
            try:
                return original_read(stored)
            finally:
                with pytest.raises(StopIteration):
                    next(guard)

    monkeypatch.setattr(applications, "read_ai_report", guarded_read)
    app.dependency_overrides[get_session] = lambda: session
    try:
        with TestClient(app) as client:
            import sys

            monkeypatch.setitem(sys.modules, "llm_rag", None)
            monkeypatch.setitem(sys.modules, "gigachat", None)
            first = client.get(f"/applications/{application.id}/ai-report")
            second = client.get(f"/applications/{application.id}/ai-report")
            assert first.status_code == 200 and first.json() == second.json()
            assert first.json()["human_report"] == expected["human_report"]
            assert (
                first.json()["checks"][0]["backend_rule"]["reference_version"]
                == "ai-report-synthetic-1"
            )
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_replace_rollback_and_cascade(
    session: Session,
    application: Application,
    library_path: None,
) -> None:
    original = save(session, application)
    first_time = original.saved_at
    session.commit()
    replacement = save(session, application, "partial")
    assert replacement.application_id == original.application_id
    assert replacement.saved_at >= first_time
    assert replacement.result["completeness"] == "PARTIAL"
    session.commit()
    expected = deepcopy(replacement.result)
    bad = deepcopy(expected)
    bad["human_report"]["summary"] = "PRIVATE-INVALID-REPORT"
    with pytest.raises(AIReportError) as caught:
        save_ai_result(
            session,
            application.id,
            bad,
            currency="RUB",
            backend_revision="a" * 40,
            ai_revision="b" * 40,
        )
    assert "PRIVATE-INVALID-REPORT" not in str(caught.value)
    session.commit()
    session.expire_all()
    assert session.get(ApplicationAIResult, application.id).result == expected
    nested = session.begin_nested()
    save(session, application, "fallback")
    nested.rollback()
    assert session.get(ApplicationAIResult, application.id).result == expected
    identifier = application.id
    session.execute(delete(Application).where(Application.id == identifier))
    session.flush()
    session.expire_all()
    assert session.get(ApplicationAIResult, identifier) is None


@pytest.mark.parametrize("damage", ["application_id", "purchase", "links", "age_group"])
def test_other_or_changed_request_is_rejected(
    session: Session,
    application: Application,
    library_path: None,
    damage: str,
) -> None:
    other = make_application()
    if damage == "application_id":
        other.id = uuid4()
    elif damage == "purchase":
        other.items[0].amount = other.items[0].amount + 1
    elif damage == "age_group":
        other.age_group = "adult"
    else:
        other.items[0].diagnosis = None
    result = integration_result(other)
    with pytest.raises(AIReportError):
        save_ai_result(
            session,
            application.id,
            result,
            currency="RUB",
            backend_revision="a" * 40,
            ai_revision="b" * 40,
        )
    assert session.get(ApplicationAIResult, application.id) is None


def test_api_missing_result_and_invalid_id(session: Session, application: Application) -> None:
    app.dependency_overrides[get_session] = lambda: session
    try:
        with TestClient(app) as client:
            for identifier, status, code in (
                (str(uuid4()), 404, "not_found"),
                (str(application.id), 409, "report_not_ready"),
                ("PRIVATE-BAD-ID", 422, "invalid_request"),
            ):
                response = client.get(f"/applications/{identifier}/ai-report")
                assert response.status_code == status
                assert response.json()["errors"][0]["code"] == code
                assert "PRIVATE-BAD-ID" not in response.text
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_corrupt_stored_report_is_safe_http_error(
    session: Session,
    application: Application,
    library_path: None,
) -> None:
    stored = save(session, application)
    changed = deepcopy(stored.result)
    changed["human_report"]["title"] = {"private": "PRIVATE-INVALID-REPORT"}
    stored.result = changed
    session.flush()
    app.dependency_overrides[get_session] = lambda: session
    try:
        with TestClient(app) as client:
            response = client.get(f"/applications/{application.id}/ai-report")
            assert response.status_code == 500
            assert response.json()["errors"][0]["code"] == "http_error"
            assert "PRIVATE-INVALID-REPORT" not in response.text
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_migration_upgrade_and_downgrade(session: Session) -> None:
    connection = session.connection()
    schema = "ai_report_migration_" + uuid4().hex
    connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
    connection.execute(text("CREATE TABLE applications (id uuid PRIMARY KEY, marker text)"))
    identifier = uuid4()
    connection.execute(
        text("INSERT INTO applications VALUES (:id, 'synthetic')"), {"id": identifier}
    )
    path = Path("alembic/versions/c9d0e1f2a3b4_application_ai_results.py")
    spec = importlib.util.spec_from_file_location("ai_report_migration", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(connection)):
        module.upgrade()
        connection.execute(
            text(
                "INSERT INTO application_ai_results "
                "(application_id,result,checks,backend_revision,ai_revision) "
                "VALUES (:id,CAST(:result AS jsonb),'[]',:backend,:ai)"
            ),
            {
                "id": identifier,
                "result": json.dumps({"synthetic": True}),
                "backend": "a" * 40,
                "ai": "b" * 40,
            },
        )
        assert connection.scalar(text("SELECT count(*) FROM application_ai_results")) == 1
        module.downgrade()
        assert connection.scalar(text("SELECT marker FROM applications")) == "synthetic"
        module.upgrade()
        assert connection.scalar(text("SELECT count(*) FROM application_ai_results")) == 0
