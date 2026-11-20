"""Use Cyrillic category codes in stored checks, rules and application summaries."""

from collections.abc import Sequence

from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: str | Sequence[str] | None = "e5f6g7h8i9j0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CODES = (("A", "А"), ("B", "Б"), ("V", "В"))


def _rename_categories(pairs: tuple[tuple[str, str], ...]) -> None:
    # All labels are frozen migration constants, never request data.
    keys = [(old + suffix, new + suffix) for old, new in pairs for suffix in ("", "_amount")]
    collisions = " OR ".join(
        f"(category_shares ? '{old}' AND category_shares ? '{new}')" for old, new in keys
    )
    # Refuse ambiguous manually edited data instead of overwriting either value.
    op.execute(
        f"""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM applications WHERE {collisions}) THEN
            RAISE EXCEPTION 'Conflicting category share keys; migration cancelled';
        END IF;
        END; $$"""
    )
    for old, new in pairs:
        # Renaming preserves enum order and every existing reference to the type.
        op.execute(f"ALTER TYPE drug_category RENAME VALUE '{old}' TO '{new}'")
    for old, new in keys:
        op.execute(
            f"""UPDATE applications
            SET category_shares = (category_shares - '{old}') ||
                jsonb_build_object('{new}', category_shares -> '{old}')
            WHERE category_shares ? '{old}'"""
        )
    # Only replace category labels in summaries produced by the backend template.
    # Custom summaries and all other text columns remain intact.
    replacements = (
        (f"категория {pairs[0][0]} —", f"категория {pairs[0][1]} —"),
        (f"%, {pairs[1][0]} —", f"%, {pairs[1][1]} —"),
        (f"%, {pairs[2][0]} —", f"%, {pairs[2][1]} —"),
    )
    expression = "summary"
    for old, new in replacements:
        expression = f"replace({expression}, '{old}', '{new}')"
    op.execute(
        f"""UPDATE applications SET summary = {expression}
        WHERE summary LIKE 'Результат анализа: индикатор %; категория {pairs[0][0]} — %'"""
    )


def upgrade() -> None:
    _rename_categories(_CODES)
    op.alter_column(
        "item_checks",
        "category",
        comment="Категории А / Б / В; NULL означает, что категория не определена.",
    )


def downgrade() -> None:
    _rename_categories(tuple((new, old) for old, new in _CODES))
    op.alter_column(
        "item_checks",
        "category",
        comment="A / B / V — категории А / Б / В; NULL означает, что категория не определена.",
    )
