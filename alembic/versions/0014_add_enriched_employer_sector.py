"""add enriched_jobs.employer_sector column

Revision ID: 0014_add_enriched_emp_sector
Revises: 0013_add_source_sector
Create Date: 2026-04-26

Adds a `employer_sector` VARCHAR(64) column on `enriched_jobs`,
defaulting to 'unknown'. This is the per-lead sector tag that
classifies the EMPLOYER of the job (resolved via
_extract_employer_from_payload for aggregator leads, or the source's
employer_name for direct leads).

Why per-lead and not just per-source:
  - Aggregator sources (Adzuna, Reed, ...) feed jobs from hundreds
    of different employers. The Adzuna source row has
    sector='aggregator', but each lead it produces has its own
    real employer (Goldman Sachs, Brevan Howard, etc.) with a
    distinct sector.
  - Storing employer_sector per lead means the Sources page,
    Leads page, exports, and any future filter can group/filter
    by sector without re-resolving the employer at query time.

Migration is purely additive — server_default='unknown' so existing
rows are populated immediately. Safe to roll back.

Backfill happens in a follow-up script run after this migration:
`python3 scripts/backfill_employer_sectors.py --commit`.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0014_add_enriched_emp_sector"
down_revision = "0013_add_source_sector"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "enriched_jobs",
        sa.Column(
            "employer_sector",
            sa.String(length=64),
            nullable=False,
            server_default="unknown",
        ),
    )
    op.create_index(
        "ix_enriched_jobs_employer_sector",
        "enriched_jobs",
        ["employer_sector"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_enriched_jobs_employer_sector",
        table_name="enriched_jobs",
    )
    op.drop_column("enriched_jobs", "employer_sector")
