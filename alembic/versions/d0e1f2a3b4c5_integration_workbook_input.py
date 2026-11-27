"""Сохранённый вход AI-книги и точные суммы без округления БД."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d0e1f2a3b4c5"
down_revision: str = "c9d0e1f2a3b4"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.add_column("applications", sa.Column("integration_input", postgresql.JSONB(), nullable=True))
    op.create_check_constraint(
        "ck_application_integration_input",
        "applications",
        "integration_input IS NULL OR (jsonb_typeof(integration_input) = 'object' "
        "AND (integration_input->>'schema_version') IS NOT DISTINCT FROM 'integration-request.1')",
    )
    op.alter_column(
        "application_items", "amount", existing_type=sa.Numeric(12, 2), type_=sa.Numeric()
    )


def downgrade() -> None:
    # Не округлять уже загруженные AI-суммы и не удалять вход молча.
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM applications WHERE integration_input IS NOT NULL) "
        "OR EXISTS (SELECT 1 FROM application_items WHERE amount != round(amount, 2) "
        "OR abs(amount) >= 10000000000) THEN "
        "RAISE EXCEPTION 'AI workbook data requires export before downgrade'; END IF; END $$"
    )
    op.alter_column(
        "application_items", "amount", existing_type=sa.Numeric(), type_=sa.Numeric(12, 2)
    )
    op.drop_constraint("ck_application_integration_input", "applications", type_="check")
    op.drop_column("applications", "integration_input")
