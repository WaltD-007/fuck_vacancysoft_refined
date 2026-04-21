"""add location_review_queue table

Revision ID: 0010_add_location_review_queue
Revises: 0009_add_users_table
Create Date: 2026-04-21

Adds the table backing the "Wrong location" button on Source cards.
When an operator spots a job whose extracted location looks wrong
(e.g. "London" but the JD body clearly says New York), they flag it
from the Sources page and the enriched_job_id lands here for a
future /review UI.

No FK cascade from enriched_jobs — deliberately. If the enriched_job
is ever hard-deleted via the "Dead job" button, we want the flag
row to stay so the review log is complete. Resolved / resolved_at
are set by the future review UI.

See also migration 0009 for users table (flagged_by_user_id FK) and
the note in src/vacancysoft/api/routes/leads.py::delete_lead about
why this flag is not auto-resolved on delete.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0010_add_location_review_queue"
down_revision = "0009_add_users_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "location_review_queue",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "enriched_job_id",
            sa.String(length=36),
            sa.ForeignKey("enriched_jobs.id"),
            nullable=False,
        ),
        sa.Column(
            "flagged_by_user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "resolved",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("FALSE"),
        ),
        sa.Column("resolved_at", sa.DateTime, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_location_review_queue_enriched_job_id",
        "location_review_queue",
        ["enriched_job_id"],
    )
    op.create_index(
        "ix_location_review_queue_flagged_by_user_id",
        "location_review_queue",
        ["flagged_by_user_id"],
    )
    op.create_index(
        "ix_location_review_queue_resolved",
        "location_review_queue",
        ["resolved"],
    )
    op.create_index(
        "ix_location_review_queue_created_at",
        "location_review_queue",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_location_review_queue_created_at", table_name="location_review_queue"
    )
    op.drop_index(
        "ix_location_review_queue_resolved", table_name="location_review_queue"
    )
    op.drop_index(
        "ix_location_review_queue_flagged_by_user_id",
        table_name="location_review_queue",
    )
    op.drop_index(
        "ix_location_review_queue_enriched_job_id",
        table_name="location_review_queue",
    )
    op.drop_table("location_review_queue")
