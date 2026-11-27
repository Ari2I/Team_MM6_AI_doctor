"""Ensure item diagnosis exists on databases migrated before it was added."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d4e5f6g7h8i9"
down_revision: str | Sequence[str] | None = "c3d4e5f6g7h8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("application_items")
    }
    if "diagnosis" not in columns:
        op.add_column("application_items", sa.Column("diagnosis", sa.Text(), nullable=True))


def downgrade() -> None:
    # Previous revision already declares this column; preserve it on downgrade.
    pass
