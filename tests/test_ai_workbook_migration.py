"""Миграция сохраняет прежние данные и не округляет сумму AI-книги."""

import os
from decimal import Decimal

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.test_category_migration import connection as connection
from tests.test_category_migration import migration

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_POSTGRES_TESTS") != "1", reason="Нужен PostgreSQL и RUN_POSTGRES_TESTS=1"
)


def test_upgrade_and_lossless_downgrade_preserve_legacy_rows(connection):
    connection.execute(text("CREATE TABLE application_items (id integer, amount numeric(12,2))"))
    connection.execute(text("INSERT INTO application_items VALUES (1, 123.45)"))
    connection.execute(text("INSERT INTO applications (id, summary) VALUES (1, 'existing')"))
    module = migration("d0e1f2a3b4c5_integration_workbook_input.py")
    with Operations.context(MigrationContext.configure(connection)):
        module.upgrade()
    assert connection.execute(
        text("SELECT summary, integration_input FROM applications")
    ).one() == (
        "existing",
        None,
    )
    assert connection.scalar(text("SELECT amount FROM application_items")) == Decimal("123.45")
    connection.execute(text("INSERT INTO application_items VALUES (2, 100.125)"))
    assert connection.scalar(text("SELECT amount FROM application_items WHERE id=2")) == Decimal(
        "100.125"
    )
    with connection.begin_nested() as nested:
        with pytest.raises(DBAPIError, match="requires export"):
            with Operations.context(MigrationContext.configure(connection)):
                module.downgrade()
        nested.rollback()
    assert connection.scalar(text("SELECT amount FROM application_items WHERE id=2")) == Decimal(
        "100.125"
    )
    connection.execute(text("DELETE FROM application_items WHERE id=2"))
    with Operations.context(MigrationContext.configure(connection)):
        module.downgrade()
    assert connection.scalar(text("SELECT summary FROM applications")) == "existing"
    assert connection.scalar(text("SELECT amount FROM application_items")) == Decimal("123.45")


def test_input_schema_check_and_nonempty_input_cannot_be_downgraded(connection):
    connection.execute(text("CREATE TABLE application_items (id integer, amount numeric(12,2))"))
    connection.execute(text("INSERT INTO applications (id) VALUES (1)"))
    module = migration("d0e1f2a3b4c5_integration_workbook_input.py")
    with Operations.context(MigrationContext.configure(connection)):
        module.upgrade()
    for invalid in ("{}", "[]", "null", '{"schema_version":"unknown"}'):
        with connection.begin_nested() as nested:
            with pytest.raises(DBAPIError):
                connection.execute(
                    text("UPDATE applications SET integration_input=CAST(:v AS jsonb)"),
                    {"v": invalid},
                )
            nested.rollback()
    connection.execute(
        text(
            "UPDATE applications SET integration_input="
            '\'{"schema_version":"integration-request.1"}\'::jsonb'
        )
    )
    with connection.begin_nested() as nested:
        with pytest.raises(DBAPIError, match="requires export"):
            with Operations.context(MigrationContext.configure(connection)):
                module.downgrade()
        nested.rollback()
