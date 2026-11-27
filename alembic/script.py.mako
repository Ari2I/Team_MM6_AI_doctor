"""${message}."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | Sequence[str] | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    """Применить изменения схемы."""
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """Откатить изменения схемы."""
    ${downgrades if downgrades else "pass"}
