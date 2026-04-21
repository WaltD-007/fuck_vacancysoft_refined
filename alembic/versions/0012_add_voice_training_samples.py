"""add voice_training_samples table

Revision ID: 0012_add_voice_training_samples
Revises: 0011_add_user_campaign_prompts
Create Date: 2026-04-21

Adds operator-authored voice training samples so the campaign voice
layer can start imitating an operator's voice BEFORE any real outbound
emails have been sent (i.e. before the Graph send flow exists).

Flow:
  1. Operator opens the Campaign Builder, edits one of the 30 generated
     variants inline until the voice sounds right.
  2. Clicks "Save as training sample" — POST writes a row here.
  3. Next campaign regeneration picks up these rows via the resolver's
     voice-sample query (unioned with SentMessage.status='sent' rows).

Once real sends start writing SentMessage rows with status='sent',
the 5-per-step rolling window naturally pushes older training rows
out. Training rows are the bootstrap; real sends take over.

No FK cascade from users — the rollback story for users stays
non-cascading (consistent with the rest of the schema).
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0012_add_voice_training_samples"
down_revision = "0011_add_user_campaign_prompts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "voice_training_samples",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        # 1–5, matching the campaign sequence indices.
        sa.Column("sequence_index", sa.Integer, nullable=False),
        # One of the six campaign tones. No CHECK constraint — the API
        # layer validates against the allowed set (same pattern as
        # user_campaign_prompts.tone).
        sa.Column("tone", sa.String(length=32), nullable=False),
        sa.Column("subject", sa.String(length=500), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        # Optional link back to the lead the training was derived
        # from — lets future analytics trace "where did this voice
        # sample come from". No FK cascade: if the enriched_job is
        # deleted (via Dead job), the training sample stays (the
        # voice data is still useful signal).
        sa.Column(
            "source_enriched_job_id",
            sa.String(length=36),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    # Primary lookup index: "last N samples per (user, sequence)".
    op.create_index(
        "ix_voice_training_samples_user_seq",
        "voice_training_samples",
        ["user_id", "sequence_index"],
    )
    # Audit index: "all training samples for a user, newest first".
    op.create_index(
        "ix_voice_training_samples_user_created",
        "voice_training_samples",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_voice_training_samples_user_created",
        table_name="voice_training_samples",
    )
    op.drop_index(
        "ix_voice_training_samples_user_seq",
        table_name="voice_training_samples",
    )
    op.drop_table("voice_training_samples")
