"""add recipient_name column to sent_messages

Revision ID: 0014_add_recipient_name
Revises: 0013_add_tracking_tables
Create Date: 2026-04-29

Adds an optional ``recipient_name`` column to ``sent_messages`` so the
operator-verified hiring-manager NAME (not just the email, which we
already store) travels with the launch through to the Campaigns
tracker.

Why: the dossier's ``hiring_managers`` JSON is LLM-generated and
sometimes wrong — it's a guide, not source of truth. The operator
double-checks both name + email before launching, types both into the
Builder, and the tracker should display what they actually verified
rather than re-deriving from the dossier on every list query.

Backfill: existing rows get NULL. The list / detail endpoints fall
back to the dossier-derived name when ``recipient_name`` is NULL,
so old campaigns continue to render the same as before.

Rollback: ``alembic downgrade 0013_add_tracking_tables`` drops the
column. Any verified-name data accumulated to date is lost — but
the dossier fallback path picks up the slack on every render.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0014_add_recipient_name"
down_revision = "0013_add_tracking_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sent_messages",
        sa.Column("recipient_name", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sent_messages", "recipient_name")
