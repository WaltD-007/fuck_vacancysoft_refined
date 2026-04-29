"""add open_events + click_events tables

Revision ID: 0013_add_tracking_tables
Revises: 0012_add_voice_training_samples
Create Date: 2026-04-27

Adds two tables for open + click tracking on outbound outreach emails:

- ``open_events``  — one row per pixel-load (1×1 gif) on a sent email.
  Deduped at write time within a 60-second window per (sent_message_id)
  to absorb Outlook preview-pane double counts. Logged unauthenticated
  via ``GET /t/o/{token}``.
- ``click_events`` — one row per link-click on a sent email. NOT deduped;
  repeat clicks are a real signal. Corporate scanner pre-fetches
  (Mimecast, Microsoft Safe Links, Proofpoint, etc.) get
  ``likely_scanner=True`` set at write time so aggregate counts can
  exclude them by default. Logged unauthenticated via
  ``GET /t/c/{token}``.

Both link to ``sent_messages.id`` via FK. ``ip_hash`` stores
``HMAC_SHA256(ip_salt, ip_address)`` so we get the deterministic-per-
salt-era dedupe property without storing raw IPs.

Tenancy seam (``organization_id`` column) is intentionally deferred to
the tenancy-seam migration (currently planned as 0014/0015 once it
lands). When that migration runs it adds ``organization_id`` to these
two tables alongside every other outreach-relevant table; clean
upgrade path with default-fill.

Rollback: ``alembic downgrade 0012`` drops both tables. Any tracking
data accumulated to date is lost — but tracking is a derived signal,
not source-of-truth, so that's acceptable. The corresponding code
paths (pixel injection, /t/* endpoints) become dead code but don't
crash; they only insert rows when called.

See docs/prospero_architecture.md §"Outreach tracking" for column-
level rationale.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0013_add_tracking_tables"
down_revision = "0012_add_voice_training_samples"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── open_events ──────────────────────────────────────────────────
    op.create_table(
        "open_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "sent_message_id",
            sa.String(length=36),
            sa.ForeignKey("sent_messages.id"),
            nullable=False,
        ),
        sa.Column(
            "opened_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("user_agent", sa.Text, nullable=True),
        sa.Column("ip_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "likely_apple_mpp",
            sa.Boolean,
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_open_events_sent_message_id",
        "open_events",
        ["sent_message_id"],
    )
    op.create_index(
        "ix_open_events_opened_at",
        "open_events",
        ["opened_at"],
    )

    # ── click_events ─────────────────────────────────────────────────
    op.create_table(
        "click_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "sent_message_id",
            sa.String(length=36),
            sa.ForeignKey("sent_messages.id"),
            nullable=False,
        ),
        sa.Column("original_url", sa.Text, nullable=False),
        sa.Column(
            "clicked_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("user_agent", sa.Text, nullable=True),
        sa.Column("ip_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "likely_scanner",
            sa.Boolean,
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_click_events_sent_message_id",
        "click_events",
        ["sent_message_id"],
    )
    op.create_index(
        "ix_click_events_clicked_at",
        "click_events",
        ["clicked_at"],
    )


def downgrade() -> None:
    """Drop both tables. Idempotent — uses IF EXISTS via the index/table
    drop ordering. Safe to run if upgrade was partially applied."""
    op.drop_index("ix_click_events_clicked_at", table_name="click_events")
    op.drop_index("ix_click_events_sent_message_id", table_name="click_events")
    op.drop_table("click_events")
    op.drop_index("ix_open_events_opened_at", table_name="open_events")
    op.drop_index("ix_open_events_sent_message_id", table_name="open_events")
    op.drop_table("open_events")
