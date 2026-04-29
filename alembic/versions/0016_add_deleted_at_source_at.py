"""add deleted_at_source_at column to raw_jobs

Revision ID: 0016_add_deleted_at_source_at
Revises: 0015_add_archived_at
Create Date: 2026-04-29

Adds an audit-trail timestamp for when a RawJob was flagged
``is_deleted_at_source=True`` — populated automatically by the
end-of-run sweep introduced alongside this migration, and shown on the
"Recently deleted" Dashboard panel so operators can spot-check and
undo false positives.

Why a separate timestamp instead of reading ``updated_at``: ``updated_at``
fires on every upsert (e.g. last_seen_at refresh on rediscovery), so it
doesn't pinpoint when the row entered the deleted state. A dedicated
column keeps the audit clean.

Behaviour:
- ``deleted_at_source_at IS NULL`` → never marked dead, or undeleted
  via operator override.
- ``deleted_at_source_at IS NOT NULL`` → ``is_deleted_at_source=True``
  was set at this timestamp.

Backfill: existing rows where ``is_deleted_at_source=True`` get
``deleted_at_source_at = NULL`` (we don't have historical data on
when each was marked). The "Recently deleted" panel filter
``WHERE deleted_at_source_at >= NOW() - 7 days`` will skip them
naturally — operators only see going-forward sweeps.

Index: composite on (is_deleted_at_source, deleted_at_source_at) to
serve the recently-deleted endpoint efficiently.

Rollback: ``alembic downgrade 0015`` drops the column + index. The
``is_deleted_at_source`` flag itself stays (it's pre-existing).
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0016_add_deleted_at_source_at"
down_revision = "0015_add_archived_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "raw_jobs",
        sa.Column("deleted_at_source_at", sa.DateTime, nullable=True),
    )
    # Composite index to serve `WHERE is_deleted_at_source = TRUE
    # AND deleted_at_source_at >= NOW() - INTERVAL '7 days'` (the
    # recently-deleted Dashboard panel query).
    op.create_index(
        "ix_raw_jobs_deleted_at_source_at",
        "raw_jobs",
        ["is_deleted_at_source", "deleted_at_source_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_raw_jobs_deleted_at_source_at", table_name="raw_jobs")
    op.drop_column("raw_jobs", "deleted_at_source_at")
