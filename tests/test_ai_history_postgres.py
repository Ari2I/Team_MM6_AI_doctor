"""Повторы, запоздалый worker и атомарная история на настоящем PostgreSQL."""

import os
from copy import deepcopy
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.models import ApplicationAIHistory, ApplicationAIResult
from app.services.ai_reports import AIReportError, save_ai_result
from tests.ai_report_data import integration_result
from tests.test_ai_reports_postgres import application as application
from tests.test_ai_reports_postgres import library_path as library_path
from tests.test_ai_reports_postgres import save
from tests.test_ai_reports_postgres import session as session
from tests.test_category_migration import connection as connection
from tests.test_category_migration import migration

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_POSTGRES_TESTS") != "1", reason="Нужен PostgreSQL и RUN_POSTGRES_TESTS=1"
)


def history_count(session, application):
    return session.scalar(
        select(func.count())
        .select_from(ApplicationAIHistory)
        .where(
            ApplicationAIHistory.application_id == application.id,
        )
    )


def test_identical_save_is_idempotent_and_rollback_preserves_all_versions(
    session, application, library_path
):
    first = save(session, application)
    original = deepcopy(first.result)
    assert history_count(session, application) == 1
    save(session, application)
    assert history_count(session, application) == 1
    save(session, application, "partial")
    assert history_count(session, application) == 2
    current = deepcopy(session.get(ApplicationAIResult, application.id).result)
    with session.begin_nested() as nested:
        save(session, application, "fallback")
        assert history_count(session, application) == 3
        nested.rollback()
    assert history_count(session, application) == 2
    assert session.get(ApplicationAIResult, application.id).result == current
    assert (
        original
        in session.scalars(
            select(ApplicationAIHistory.result).where(
                ApplicationAIHistory.application_id == application.id,
            )
        ).all()
    )


def test_late_previous_run_cannot_overwrite_new_report(session, application, library_path):
    old_run, new_run = uuid4(), uuid4()
    application.analysis_run_id = new_run
    session.flush()
    saved = save(session, application, "partial")
    current = deepcopy(saved.result)
    with pytest.raises(AIReportError):
        save_ai_result(
            session,
            application.id,
            integration_result(application),
            currency="RUB",
            backend_revision="a" * 40,
            ai_revision="b" * 40,
            expected_run_id=old_run,
        )
    assert history_count(session, application) == 1
    assert session.get(ApplicationAIResult, application.id).result == current


def test_history_migration_refuses_silent_data_loss(connection):
    schema = "history_migration_" + uuid4().hex
    connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
    connection.execute(text("CREATE TABLE applications (id uuid PRIMARY KEY)"))
    identifier = uuid4()
    connection.execute(text("INSERT INTO applications (id) VALUES (:id)"), {"id": identifier})
    module = migration("e1f2a3b4c5d6_ai_result_history.py")
    with Operations.context(MigrationContext.configure(connection)):
        module.upgrade()
    assert connection.scalar(text("SELECT analysis_run_id FROM applications")) is None
    connection.execute(
        text(
            "INSERT INTO application_ai_history VALUES "
            "(:id, :application, :hash, '{}'::jsonb, '[]'::jsonb, "
            ":backend, :ai, now())"
        ),
        {
            "id": uuid4(),
            "application": identifier,
            "hash": "f" * 64,
            "backend": "a" * 40,
            "ai": "b" * 40,
        },
    )
    with connection.begin_nested() as nested:
        with pytest.raises(DBAPIError, match="requires export"):
            with Operations.context(MigrationContext.configure(connection)):
                module.downgrade()
        nested.rollback()
    assert connection.scalar(text("SELECT count(*) FROM application_ai_history")) == 1
    connection.execute(text("DELETE FROM application_ai_history"))
    with Operations.context(MigrationContext.configure(connection)):
        module.downgrade()
    assert connection.scalar(text("SELECT count(*) FROM applications")) == 1
