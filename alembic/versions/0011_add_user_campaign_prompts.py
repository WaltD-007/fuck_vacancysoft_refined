"""add user_campaign_prompts table

Revision ID: 0011_add_user_campaign_prompts
Revises: 0010_add_location_review_queue
Create Date: 2026-04-21

Adds per-user, per-tone voice guidance that the campaign prompt
resolver injects into the LLM prompt whenever an operator regenerates
a campaign. One row per ``(user_id, tone)`` — six rows per user max.

When ``instructions_text`` is empty or absent, the base campaign
template's default voice guidance for that tone is used unchanged —
i.e. cold-start / new-user output is byte-identical to today.

Voice samples (the last five actually-sent messages per sequence) are
NOT stored here — they're queried live from ``sent_messages`` in the
resolver. No samples table. See
.claude/plans/linear-meandering-rossum.md for full design.

Depends on 0009 (users) and 0010 (location_review_queue). The FK from
``user_id`` to ``users.id`` pins data safety: if a user is removed the
rows get orphaned rather than preventing the delete (no cascade set,
matching the rest of the Prospero schema's additive-only migration
policy).
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0011_add_user_campaign_prompts"
down_revision = "0010_add_location_review_queue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_campaign_prompts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "tone",
            sa.String(length=32),
            nullable=False,
        ),
        # Free-form voice guidance, e.g. "I keep mine short, I use
        # 'cheers', I never say 'touching base'". Empty string means
        # "fall back to the template's default guidance for this tone".
        sa.Column(
            "instructions_text",
            sa.Text,
            nullable=False,
            server_default=sa.text("''"),
        ),
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
    # Unique on (user_id, tone) — caps each user at six rows, one per
    # tone, and makes upserts in the PUT endpoint cheap.
    op.create_index(
        "ix_user_campaign_prompts_user_id_tone",
        "user_campaign_prompts",
        ["user_id", "tone"],
        unique=True,
    )
    # Plain user_id index for the "load all six for this user" query
    # the resolver runs on every operator-triggered campaign regen.
    op.create_index(
        "ix_user_campaign_prompts_user_id",
        "user_campaign_prompts",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_campaign_prompts_user_id",
        table_name="user_campaign_prompts",
    )
    op.drop_index(
        "ix_user_campaign_prompts_user_id_tone",
        table_name="user_campaign_prompts",
    )
    op.drop_table("user_campaign_prompts")
