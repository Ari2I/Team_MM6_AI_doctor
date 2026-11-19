"""Upgrade and downgrade category codes without losing existing PostgreSQL data."""

import importlib.util
import os
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_POSTGRES_TESTS") != "1", reason="Нужен PostgreSQL и RUN_POSTGRES_TESTS=1"
)


def migration() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[1] / "alembic/versions/f6a7b8c9d0e1_cyrillic_categories.py"
    )
    spec = importlib.util.spec_from_file_location("cyrillic_categories", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def connection() -> Iterator[Connection]:
    from app.db.session import engine

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            # All objects belong to a unique temporary schema; rollback removes it.
            schema = "category_migration_" + uuid4().hex
            connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(sa.text(f'SET LOCAL search_path TO "{schema}"'))
            connection.execute(sa.text("CREATE TYPE drug_category AS ENUM ('A', 'B', 'V')"))
            connection.execute(
                sa.text(
                    "CREATE TABLE applications (id integer PRIMARY KEY, category_shares jsonb, "
                    "summary text, status text DEFAULT 'analyzed')"
                )
            )
            for table in ("item_checks", "reference_rules"):
                connection.execute(
                    sa.text(
                        f"CREATE TABLE {table} (id integer PRIMARY KEY, category drug_category)"
                    )
                )
                connection.execute(
                    sa.text(f"INSERT INTO {table} VALUES (1, 'A'), (2, 'B'), (3, 'V'), (4, NULL)")
                )
            yield connection
        finally:
            transaction.rollback()


def test_upgrade_and_downgrade_preserve_existing_data(connection: Connection) -> None:
    original = {
        "A": 90,
        "B": 5,
        "V": 5,
        "A_amount": 900,
        "B_amount": 50,
        "V_amount": 50,
        "undetermined": 0,
        "undetermined_amount": 0,
        "total_amount": 1000,
        "extra": 42,
    }
    summary = (
        "Результат анализа: индикатор green; категория A — 90.00%, B — 5.00%, V — 5.00%. "
        "Результат носит рекомендательный характер."
    )
    insert = sa.text("INSERT INTO applications (id, category_shares, summary) VALUES (:id, :s, :t)")
    insert = insert.bindparams(sa.bindparam("s", type_=JSONB()))
    connection.execute(
        insert,
        [
            {"id": 1, "s": original, "t": summary},
            {"id": 2, "s": {}, "t": "Произвольный текст: категория A"},
            {"id": 3, "s": {"A": 0, "B": None}, "t": None},
        ],
    )
    connection.execute(sa.text("INSERT INTO applications (id) VALUES (4)"))
    before = connection.execute(sa.text("SELECT * FROM applications ORDER BY id")).mappings().all()
    module = migration()
    with Operations.context(MigrationContext.configure(connection)):
        module.upgrade()
    expected = {
        {"A": "А", "B": "Б", "V": "В"}.get(key[0], key[0]) + key[1:]: value
        for key, value in original.items()
    }
    after = connection.execute(sa.text("SELECT * FROM applications ORDER BY id")).mappings().all()
    assert after[0]["category_shares"] == expected
    assert "категория А — 90.00%, Б — 5.00%, В — 5.00%" in after[0]["summary"]
    assert after[1] == before[1]  # Empty object and arbitrary text stay unchanged.
    assert after[2]["category_shares"] == {"А": 0, "Б": None}
    assert after[3] == before[3]  # SQL NULL stays NULL.
    assert [row["status"] for row in after] == [row["status"] for row in before]
    for table in ("item_checks", "reference_rules"):
        assert connection.execute(
            sa.text(f"SELECT category::text FROM {table} ORDER BY id")
        ).scalars().all() == ["А", "Б", "В", None]
    with Operations.context(MigrationContext.configure(connection)):
        module.downgrade()
    assert (
        connection.execute(sa.text("SELECT * FROM applications ORDER BY id")).mappings().all()
        == before
    )
    for table in ("item_checks", "reference_rules"):
        assert connection.execute(
            sa.text(f"SELECT category::text FROM {table} ORDER BY id")
        ).scalars().all() == ["A", "B", "V", None]


def test_conflicting_keys_abort_without_overwriting_data(connection: Connection) -> None:
    connection.execute(
        sa.text("INSERT INTO applications (id, category_shares) VALUES (1, :shares)").bindparams(
            sa.bindparam("shares", type_=JSONB())
        ),
        {"shares": {"A": 80, "А": 90}},
    )
    with connection.begin_nested() as savepoint:
        with pytest.raises(DBAPIError, match="Conflicting category share keys"):
            with Operations.context(MigrationContext.configure(connection)):
                migration().upgrade()
        savepoint.rollback()
    assert connection.scalar(sa.text("SELECT category_shares FROM applications")) == {
        "A": 80,
        "А": 90,
    }
    assert connection.scalar(sa.text("SELECT category::text FROM item_checks WHERE id=1")) == "A"
