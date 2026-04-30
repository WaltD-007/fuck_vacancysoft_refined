"""add is_psl flag to sources

Revision ID: 0017_add_is_psl_to_sources
Revises: 0016_add_deleted_at_source_at
Create Date: 2026-04-30

Adds a boolean ``is_psl`` flag to ``sources`` so operators can mark
companies for their Preferred Supplier List — a manually-curated set
of accounts targeted for BD outreach. PSL is a flag, not a bucket: a
company stays visible in its native lead state (With Leads / No Jobs
Found / Not Relevant / Broken) and additionally appears when the
operator selects the PSL view on the Sources page.

Schema:
- ``is_psl boolean NOT NULL DEFAULT false`` on ``sources``.
- Partial index on ``WHERE is_psl`` so the PSL filter scan stays cheap
  even at 30k+ sources (the index only stores PSL rows, typically <100).

Audit columns (psl_added_at / psl_added_by_user_id) intentionally
NOT included in this migration — operator preference 2026-04-30
('park'). Add later if accountability becomes a need.

Rollback: ``alembic downgrade 0016`` drops the column + index. No
data lost (PSL is the only consumer of the column).
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0017_add_is_psl_to_sources"
down_revision = "0016_add_deleted_at_source_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sources",
        sa.Column("is_psl", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    # Partial index — only stores rows where is_psl is true. Saves
    # 99%+ of index size on typical datasets where PSL is a small
    # curated set inside a much larger source pool.
    op.create_index(
        "ix_sources_is_psl",
        "sources",
        ["is_psl"],
        postgresql_where=sa.text("is_psl"),
    )


def downgrade() -> None:
    op.drop_index("ix_sources_is_psl", table_name="sources")
    op.drop_column("sources", "is_psl")
