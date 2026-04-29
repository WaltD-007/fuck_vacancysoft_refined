"""add archived_at column to campaign_outputs

Revision ID: 0015_add_archived_at
Revises: 0014_add_recipient_name
Create Date: 2026-04-29

Adds soft-archive support for campaign rows on the Campaigns tracker.

Why: terminal campaigns (replied / no-response / cancelled) clutter the
active view once an operator is running 30-50 sequences in parallel.
Hard-deleting them loses the engagement history (opens, clicks,
replies). Soft archive hides them from the default list while
preserving every row.

Behaviour:
- ``archived_at IS NULL`` → active (shown in default list)
- ``archived_at IS NOT NULL`` → archived (hidden unless ``?archived=true``
  or ``?archived=all`` on the list endpoint)

Archiving requires the campaign to be in a terminal state — pending
sends must be cancelled first via the existing /cancel endpoint. The
archive endpoint enforces this with a 422; preventing the corner case
where an archived campaign still has deferred ARQ jobs firing.

Backfill: existing rows get NULL (active). No behavioural change for
campaigns that haven't been explicitly archived.

Rollback: ``alembic downgrade 0014`` drops the column. Any archive
state set since deploy is lost; campaigns just become visible again.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0015_add_archived_at"
down_revision = "0014_add_recipient_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "campaign_outputs",
        sa.Column("archived_at", sa.DateTime, nullable=True),
    )
    # Indexed because the list endpoint filters by `archived_at IS NULL`
    # on every request — most campaigns will be active so a regular
    # btree is fine; switch to a partial index if archived volume grows.
    op.create_index(
        "ix_campaign_outputs_archived_at",
        "campaign_outputs",
        ["archived_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_campaign_outputs_archived_at", table_name="campaign_outputs")
    op.drop_column("campaign_outputs", "archived_at")
