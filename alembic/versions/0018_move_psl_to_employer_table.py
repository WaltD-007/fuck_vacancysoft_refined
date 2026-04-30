"""move PSL flag from sources.is_psl to a dedicated psl_employers table

Revision ID: 0018_move_psl_to_employer_table
Revises: 0017_add_is_psl_to_sources
Create Date: 2026-04-30

Why
---
The PSL flag landed in 0017 as a boolean on ``sources``. Operator
feedback (within the hour): aggregator-only cards have no ``sources``
row of their own and so couldn't be PSL-flagged from the UI.

PSL is conceptually about the EMPLOYER, not the scrape source —
"BD-target Look Ahead" doesn't depend on whether we found them via a
direct Workday board or via Adzuna. So we move the flag from
``sources.is_psl`` (per-source) to ``psl_employers`` (per-employer,
keyed on the same normalised name the source-card ledger dedupes on).

Schema
------
- New table ``psl_employers``:
  - ``id SERIAL PRIMARY KEY``
  - ``employer_norm TEXT NOT NULL UNIQUE`` — lowercase, stripped, the
    same key used by ``_build_source_card_ledger`` to dedupe cards.
  - ``employer_display TEXT NOT NULL`` — cased version for the UI.
  - ``added_at TIMESTAMP NOT NULL DEFAULT NOW()`` — added now, not
    parking audit accountability columns this time round (the
    operator-deferred ones from 0017 were ``psl_added_by_user_id``).
- Drop ``sources.is_psl`` and its partial index. Empty in practice
  (0017 shipped about an hour ago and no production data depends on
  the column), so no backfill needed.

Rollback
--------
``alembic downgrade 0017`` re-creates ``sources.is_psl`` (default
false, no data) and drops ``psl_employers``. PSL state will be lost,
which is fine since the table only ever had the post-0018 entries.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0018_move_psl_to_employer_table"
down_revision = "0017_add_is_psl_to_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "psl_employers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("employer_norm", sa.Text(), nullable=False, unique=True),
        sa.Column("employer_display", sa.Text(), nullable=False),
        sa.Column(
            "added_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("ix_psl_employers_employer_norm", "psl_employers", ["employer_norm"])

    op.drop_index("ix_sources_is_psl", table_name="sources")
    op.drop_column("sources", "is_psl")


def downgrade() -> None:
    op.add_column(
        "sources",
        sa.Column("is_psl", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_index(
        "ix_sources_is_psl",
        "sources",
        ["is_psl"],
        postgresql_where=sa.text("is_psl"),
    )
    op.drop_index("ix_psl_employers_employer_norm", table_name="psl_employers")
    op.drop_table("psl_employers")
