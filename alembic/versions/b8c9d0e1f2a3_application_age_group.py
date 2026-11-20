"""Store the optional age group required to select applicable recommendations."""

import sqlalchemy as sa

from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: str = "a7b8c9d0e1f2"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.add_column("applications", sa.Column("age_group", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("applications", "age_group")
