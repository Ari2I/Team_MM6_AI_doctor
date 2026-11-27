"""Хранение последнего AI-результата и снимка backend-проверок заявки."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c9d0e1f2a3b4"
down_revision: str = "b8c9d0e1f2a3"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.create_table(
        "application_ai_results",
        sa.Column(
            "application_id",
            sa.Uuid(),
            sa.ForeignKey("applications.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column("checks", postgresql.JSONB(), nullable=False),
        sa.Column("backend_revision", sa.String(40), nullable=False),
        sa.Column("ai_revision", sa.String(40), nullable=False),
        sa.Column(
            "saved_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("jsonb_typeof(result) = 'object'", name="ck_ai_result_object"),
        sa.CheckConstraint("jsonb_typeof(checks) = 'array'", name="ck_ai_checks_array"),
    )


def downgrade() -> None:
    op.drop_table("application_ai_results")
