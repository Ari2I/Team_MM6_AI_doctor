"""Сохраняемые основания и версия набора для демонстрационного API."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision = "d0711_demo_evidence"
down_revision = "c3d4e5f6g7h8"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("applications", sa.Column("demo_meta", JSONB()))
    op.add_column("application_items", sa.Column("original_cells", JSONB()))
    op.add_column("item_checks", sa.Column("evidence", JSONB()))
    op.add_column("reference_versions", sa.Column("fingerprint", sa.Text()))


def downgrade():
    op.drop_column("reference_versions", "fingerprint")
    op.drop_column("item_checks", "evidence")
    op.drop_column("application_items", "original_cells")
    op.drop_column("applications", "demo_meta")
