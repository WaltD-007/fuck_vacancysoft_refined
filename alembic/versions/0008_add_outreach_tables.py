"""add outreach email tables: sent_messages, received_replies

Revision ID: 0008_add_outreach_tables
Revises: 0007_add_sub_specialism
Create Date: 2026-04-21

Adds two tables that track the Microsoft Graph outreach lifecycle:

- ``sent_messages``      — one row per scheduled/sent outbound email
- ``received_replies``   — one row per Graph-observed inbound reply

Zero impact on existing rows or queries. Safe to apply ahead of
Keybridge/security approval — the Graph client (added in PR A) runs
in dry-run mode until OUTREACH_DRY_RUN=false, so any rows written
to sent_messages before go-live will carry ``graph_message_id``
values like ``dryrun-msg-<uuid>`` that are obviously synthetic.

See docs/outreach_email.md §2.4 for column-level rationale.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0008_add_outreach_tables"
down_revision = "0007_add_sub_specialism"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── sent_messages ─────────────────────────────────────────────
    op.create_table(
        "sent_messages",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "campaign_output_id",
            sa.String(length=36),
            sa.ForeignKey("campaign_outputs.id"),
            nullable=False,
        ),
        sa.Column("sender_user_id", sa.String(length=255), nullable=False),
        sa.Column("recipient_email", sa.String(length=320), nullable=False),
        sa.Column("sequence_index", sa.Integer, nullable=False),
        sa.Column("tone", sa.String(length=32), nullable=False),
        sa.Column("scheduled_for", sa.DateTime, nullable=False),
        sa.Column("sent_at", sa.DateTime, nullable=True),
        sa.Column("graph_message_id", sa.String(length=255), nullable=True),
        sa.Column("conversation_id", sa.String(length=255), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("arq_job_id", sa.String(length=64), nullable=True),
        sa.Column("subject", sa.String(length=500), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_sent_messages_campaign_output_id", "sent_messages", ["campaign_output_id"]
    )
    op.create_index(
        "ix_sent_messages_sender_user_id", "sent_messages", ["sender_user_id"]
    )
    op.create_index(
        "ix_sent_messages_scheduled_for", "sent_messages", ["scheduled_for"]
    )
    op.create_index("ix_sent_messages_sent_at", "sent_messages", ["sent_at"])
    op.create_index(
        "ix_sent_messages_conversation_id", "sent_messages", ["conversation_id"]
    )
    op.create_index("ix_sent_messages_status", "sent_messages", ["status"])
    op.create_index(
        "ix_sent_messages_arq_job_id", "sent_messages", ["arq_job_id"]
    )

    # ── received_replies ──────────────────────────────────────────
    op.create_table(
        "received_replies",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("conversation_id", sa.String(length=255), nullable=False),
        sa.Column("sender_user_id", sa.String(length=255), nullable=False),
        sa.Column(
            "graph_message_id",
            sa.String(length=255),
            nullable=False,
            unique=True,
        ),
        sa.Column("from_email", sa.String(length=320), nullable=False),
        sa.Column("received_at", sa.DateTime, nullable=False),
        sa.Column("subject", sa.String(length=500), nullable=True),
        sa.Column(
            "matched_sent_message_id",
            sa.String(length=36),
            sa.ForeignKey("sent_messages.id"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_received_replies_conversation_id",
        "received_replies",
        ["conversation_id"],
    )
    op.create_index(
        "ix_received_replies_sender_user_id",
        "received_replies",
        ["sender_user_id"],
    )
    op.create_index(
        "ix_received_replies_received_at",
        "received_replies",
        ["received_at"],
    )


def downgrade() -> None:
    # ``received_replies`` first because of the FK back to sent_messages.
    op.drop_index("ix_received_replies_received_at", table_name="received_replies")
    op.drop_index("ix_received_replies_sender_user_id", table_name="received_replies")
    op.drop_index("ix_received_replies_conversation_id", table_name="received_replies")
    op.drop_table("received_replies")

    op.drop_index("ix_sent_messages_arq_job_id", table_name="sent_messages")
    op.drop_index("ix_sent_messages_status", table_name="sent_messages")
    op.drop_index("ix_sent_messages_conversation_id", table_name="sent_messages")
    op.drop_index("ix_sent_messages_sent_at", table_name="sent_messages")
    op.drop_index("ix_sent_messages_scheduled_for", table_name="sent_messages")
    op.drop_index("ix_sent_messages_sender_user_id", table_name="sent_messages")
    op.drop_index("ix_sent_messages_campaign_output_id", table_name="sent_messages")
    op.drop_table("sent_messages")
