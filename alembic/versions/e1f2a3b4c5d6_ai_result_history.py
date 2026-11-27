"""История AI-результатов и защита от запоздалого сохранения прошлого запуска."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: str = "d0e1f2a3b4c5"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.add_column("applications", sa.Column("analysis_run_id", sa.Uuid(), nullable=True))
    op.create_table(
        "application_ai_history",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "application_id",
            sa.Uuid(),
            sa.ForeignKey("applications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column("checks", postgresql.JSONB(), nullable=False),
        sa.Column("backend_revision", sa.String(40), nullable=False),
        sa.Column("ai_revision", sa.String(40), nullable=False),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("application_id", "fingerprint", name="uq_ai_history_version"),
    )
    op.create_index(
        "ix_application_ai_history_application_id", "application_ai_history", ["application_id"]
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM application_ai_history) THEN "
        "RAISE EXCEPTION 'AI history requires export before downgrade'; END IF; END $$"
    )
    op.drop_table("application_ai_history")
    op.drop_column("applications", "analysis_run_id")
