"""Add reference metadata and persisted normalized lookup values."""

import re
import unicodedata
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e5f6g7h8i9j0"
down_revision: str | Sequence[str] | None = "d4e5f6g7h8i9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("reference_versions", sa.Column("name", sa.Text(), nullable=True))
    op.add_column("reference_versions", sa.Column("description", sa.Text(), nullable=True))
    op.add_column(
        "reference_versions",
        sa.Column("is_synthetic", sa.Boolean(), server_default=sa.text("true"), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE reference_versions SET name = 'Демонстрационные данные; "
            "не клинические рекомендации (' || version || ')', "
            "description = 'Демонстрационные данные; не клинические рекомендации.', "
            "is_synthetic = true"
        )
    )
    op.alter_column("reference_versions", "name", nullable=False)
    op.alter_column("reference_versions", "description", nullable=False)
    op.alter_column("reference_versions", "is_synthetic", nullable=False)
    op.add_column("reference_drugs", sa.Column("normalized_trade_name", sa.Text(), nullable=True))
    op.add_column("reference_drugs", sa.Column("normalized_mnn", sa.Text(), nullable=True))
    op.add_column(
        "reference_diagnoses", sa.Column("normalized_diagnosis_id", sa.Text(), nullable=True)
    )
    op.add_column("reference_diagnoses", sa.Column("normalized_name", sa.Text(), nullable=True))
    op.add_column("reference_rules", sa.Column("normalized_mnn", sa.Text(), nullable=True))
    op.add_column("reference_rules", sa.Column("normalized_diagnosis_id", sa.Text(), nullable=True))
    # Frozen normalize_text algorithm for repeatable migration of existing rows.
    for table, fields in (
        ("reference_drugs", ("trade_name", "mnn")),
        ("reference_diagnoses", ("diagnosis_id", "name")),
        ("reference_rules", ("mnn", "diagnosis_id")),
    ):
        rows = op.get_bind().execute(sa.text(f"SELECT id, {', '.join(fields)} FROM {table}"))
        for row in rows.mappings():
            values = {"id": row["id"]}
            for field in fields:
                value = unicodedata.normalize("NFKC", row[field]).casefold().replace("ё", "е")
                values[f"normalized_{field}"] = re.sub(r"\s+", " ", value).strip()
            setters = ", ".join(f"normalized_{field} = :normalized_{field}" for field in fields)
            op.get_bind().execute(sa.text(f"UPDATE {table} SET {setters} WHERE id = :id"), values)
    for table, column in (
        ("reference_drugs", "normalized_trade_name"),
        ("reference_drugs", "normalized_mnn"),
        ("reference_diagnoses", "normalized_diagnosis_id"),
        ("reference_diagnoses", "normalized_name"),
        ("reference_rules", "normalized_mnn"),
        ("reference_rules", "normalized_diagnosis_id"),
    ):
        op.alter_column(table, column, nullable=False)
    op.create_index(
        "uq_reference_versions_active",
        "reference_versions",
        ["is_active"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )


def downgrade() -> None:
    op.drop_index("uq_reference_versions_active", table_name="reference_versions")
    for table, column in (
        ("reference_rules", "normalized_diagnosis_id"),
        ("reference_rules", "normalized_mnn"),
        ("reference_diagnoses", "normalized_name"),
        ("reference_diagnoses", "normalized_diagnosis_id"),
        ("reference_drugs", "normalized_mnn"),
        ("reference_drugs", "normalized_trade_name"),
    ):
        op.drop_column(table, column)
    op.drop_column("reference_versions", "is_synthetic")
    op.drop_column("reference_versions", "description")
    op.drop_column("reference_versions", "name")
