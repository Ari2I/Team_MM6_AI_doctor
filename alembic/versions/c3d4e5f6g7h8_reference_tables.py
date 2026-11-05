"""Add versioned synthetic reference tables."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c3d4e5f6g7h8"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reference_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version"),
    )
    op.create_table(
        "reference_drugs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("trade_name", sa.Text(), nullable=False),
        sa.Column("mnn", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["version_id"], ["reference_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reference_drugs_version_id", "reference_drugs", ["version_id"])
    op.create_table(
        "reference_diagnoses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("diagnosis_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["version_id"], ["reference_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reference_diagnoses_version_id", "reference_diagnoses", ["version_id"])
    op.create_table(
        "reference_rules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("mnn", sa.Text(), nullable=False),
        sa.Column("diagnosis_id", sa.Text(), nullable=False),
        sa.Column(
            "category",
            postgresql.ENUM("A", "B", "V", name="drug_category", create_type=False),
            nullable=True,
        ),
        sa.Column("source", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["version_id"], ["reference_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version_id", "mnn", "diagnosis_id", name="uq_reference_rule"),
    )
    op.create_index("ix_reference_rules_version_id", "reference_rules", ["version_id"])


def downgrade() -> None:
    op.drop_index("ix_reference_rules_version_id", table_name="reference_rules")
    op.drop_table("reference_rules")
    op.drop_index("ix_reference_diagnoses_version_id", table_name="reference_diagnoses")
    op.drop_table("reference_diagnoses")
    op.drop_index("ix_reference_drugs_version_id", table_name="reference_drugs")
    op.drop_table("reference_drugs")
    op.drop_table("reference_versions")
