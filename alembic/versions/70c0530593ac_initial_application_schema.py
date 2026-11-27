"""initial_application_schema."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "70c0530593ac"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Применить изменения схемы."""
    op.create_table(
        "applications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("uploaded", "parsed", "analyzed", "error", name="application_status"),
            server_default="uploaded",
            nullable=False,
        ),
        sa.Column(
            "traffic_light",
            sa.Enum("green", "yellow", "red", "undetermined", name="traffic_light"),
            nullable=True,
        ),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("secretary_decision", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "application_diagnoses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_application_diagnoses_application_id"),
        "application_diagnoses",
        ["application_id"],
        unique=False,
    )
    op.create_table(
        "validation_issues",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("level", sa.Enum("critical", "warning", name="issue_level"), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("row", sa.Integer(), nullable=True),
        sa.Column("column", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_validation_issues_application_id"),
        "validation_issues",
        ["application_id"],
        unique=False,
    )
    op.create_table(
        "application_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("diagnosis_id", sa.Uuid(), nullable=True),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("drug_name", sa.Text(), nullable=True),
        sa.Column("dosage", sa.Text(), nullable=True),
        sa.Column("quantity", sa.Numeric(), nullable=True),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("mnn", sa.Text(), nullable=True),
        sa.Column(
            "category",
            sa.Enum("A", "B", "V", name="drug_category"),
            nullable=True,
            comment="A / B / V — русские категории А / Б / В соответственно.",
        ),
        sa.Column("confidence", sa.Double(), nullable=True),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("reference_version", sa.Text(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("needs_review", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("review_reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["diagnosis_id"], ["application_diagnoses.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_application_items_application_id"),
        "application_items",
        ["application_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_application_items_diagnosis_id"),
        "application_items",
        ["diagnosis_id"],
        unique=False,
    )


def downgrade() -> None:
    """Откатить изменения схемы."""
    op.drop_index(op.f("ix_application_items_diagnosis_id"), table_name="application_items")
    op.drop_index(op.f("ix_application_items_application_id"), table_name="application_items")
    op.drop_table("application_items")
    op.drop_index(op.f("ix_validation_issues_application_id"), table_name="validation_issues")
    op.drop_table("validation_issues")
    op.drop_index(
        op.f("ix_application_diagnoses_application_id"), table_name="application_diagnoses"
    )
    op.drop_table("application_diagnoses")
    op.drop_table("applications")
    # Alembic не удаляет PostgreSQL enum автоматически при op.drop_table.
    sa.Enum(name="drug_category").drop(op.get_bind(), checkfirst=False)
    sa.Enum(name="issue_level").drop(op.get_bind(), checkfirst=False)
    sa.Enum(name="traffic_light").drop(op.get_bind(), checkfirst=False)
    sa.Enum(name="application_status").drop(op.get_bind(), checkfirst=False)
