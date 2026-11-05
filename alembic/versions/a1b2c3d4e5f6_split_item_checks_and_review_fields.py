"""split diagnosis checks from application items and add review summary fields.

Result columns that used to live on ``application_items`` are intentionally
dropped.  Existing values in those columns (and the old item-to-diagnosis
link) may be lost; the new model stores pair-specific results in ``item_checks``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "70c0530593ac"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the split between drug matching and pair checks."""
    # Existing result values are intentionally discarded as described above.
    op.drop_constraint(
        "application_items_diagnosis_id_fkey", "application_items", type_="foreignkey"
    )
    op.drop_index(op.f("ix_application_items_diagnosis_id"), table_name="application_items")
    for column in (
        "diagnosis_id",
        "category",
        "confidence",
        "source",
        "reference_version",
        "comment",
        "needs_review",
        "review_reason",
    ):
        op.drop_column("application_items", column)
    op.add_column("application_items", sa.Column("match_confidence", sa.Float(), nullable=True))
    op.add_column("application_items", sa.Column("match_note", sa.Text(), nullable=True))
    op.add_column("application_items", sa.Column("diagnosis", sa.Text(), nullable=True))

    op.drop_column("applications", "secretary_decision")
    op.add_column(
        "applications",
        sa.Column("requires_review", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("applications", sa.Column("warning_message", sa.Text(), nullable=True))
    op.add_column("applications", sa.Column("category_shares", postgresql.JSONB(), nullable=True))

    op.create_table(
        "item_checks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("diagnosis_id", sa.Uuid(), nullable=False),
        sa.Column(
            "category",
            postgresql.ENUM("A", "B", "V", name="drug_category", create_type=False),
            nullable=True,
        ),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("reference_version", sa.Text(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("needs_review", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("review_reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["item_id"], ["application_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["diagnosis_id"], ["application_diagnoses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("item_id", "diagnosis_id", name="uq_item_checks_item_diagnosis"),
    )
    op.create_index(op.f("ix_item_checks_item_id"), "item_checks", ["item_id"], unique=False)
    op.create_index(
        op.f("ix_item_checks_diagnosis_id"), "item_checks", ["diagnosis_id"], unique=False
    )


def downgrade() -> None:
    """Restore the previous item-level result columns."""
    op.drop_index(op.f("ix_item_checks_diagnosis_id"), table_name="item_checks")
    op.drop_index(op.f("ix_item_checks_item_id"), table_name="item_checks")
    op.drop_table("item_checks")

    op.drop_column("applications", "category_shares")
    op.drop_column("applications", "warning_message")
    op.drop_column("applications", "requires_review")
    op.add_column("applications", sa.Column("secretary_decision", sa.Text(), nullable=True))

    op.drop_column("application_items", "match_note")
    op.drop_column("application_items", "diagnosis")
    op.drop_column("application_items", "match_confidence")
    op.add_column("application_items", sa.Column("diagnosis_id", sa.Uuid(), nullable=True))
    op.add_column(
        "application_items",
        sa.Column(
            "category",
            postgresql.ENUM("A", "B", "V", name="drug_category", create_type=False),
            nullable=True,
        ),
    )
    op.add_column("application_items", sa.Column("confidence", sa.Double(), nullable=True))
    op.add_column("application_items", sa.Column("source", sa.Text(), nullable=True))
    op.add_column("application_items", sa.Column("reference_version", sa.Text(), nullable=True))
    op.add_column("application_items", sa.Column("comment", sa.Text(), nullable=True))
    op.add_column(
        "application_items",
        sa.Column("needs_review", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("application_items", sa.Column("review_reason", sa.Text(), nullable=True))
    op.create_foreign_key(
        "application_items_diagnosis_id_fkey",
        "application_items",
        "application_diagnoses",
        ["diagnosis_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_application_items_diagnosis_id"),
        "application_items",
        ["diagnosis_id"],
        unique=False,
    )
