"""add source.sector column

Revision ID: 0013_add_source_sector
Revises: 0012_add_voice_training_samples
Create Date: 2026-04-26

Adds a `sector` VARCHAR(64) column on `sources`, defaulting to
'unknown'. This is the structural enabler for industry-level filtering
on the Sources page (Phase 2 of the work — see
/Users/antonyberou/.claude/plans/sector-tagging-plan.md).

Why:
  - The schema has no industry/sector data anywhere; ad-hoc filters
    for "all hedge funds" / "all insurers" rely on string matching
    `employer_name` against keyword lists, which is fragile (Capital
    One vs Capital Group both match `%capital%`).
  - The mapping is curated in `configs/sector_taxonomy.yaml` with
    ~30 sector buckets; a `detect_sector()` function in
    `src/vacancysoft/source_registry/sector_classifier.py` reads the
    YAML and returns the right bucket.

Migration is purely additive — `server_default='unknown'` so existing
rows are populated immediately at upgrade time. Safe to roll back.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0013_add_source_sector"
down_revision = "0012_add_voice_training_samples"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sources",
        sa.Column(
            "sector",
            sa.String(length=64),
            nullable=False,
            server_default="unknown",
        ),
    )
    op.create_index("ix_sources_sector", "sources", ["sector"])


def downgrade() -> None:
    op.drop_index("ix_sources_sector", table_name="sources")
    op.drop_column("sources", "sector")
