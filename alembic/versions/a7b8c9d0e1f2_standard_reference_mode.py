"""Default to ordinary references and deactivate previously enabled synthetic data."""

import sqlalchemy as sa

from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: str = "f6a7b8c9d0e1"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.alter_column("reference_versions", "is_synthetic", server_default=sa.text("false"))
    # Keep all references and past application results; only stop selecting
    # synthetic rules for new submissions during the transition.
    op.execute("UPDATE reference_versions SET is_active = false WHERE is_synthetic = true")


def downgrade() -> None:
    op.alter_column("reference_versions", "is_synthetic", server_default=sa.text("true"))
    # Activation is an operator choice; do not silently reactivate old rules.
