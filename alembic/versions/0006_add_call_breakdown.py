"""add call_breakdown JSON to intelligence_dossiers

Revision ID: 0006_add_call_breakdown
Revises: 0005_add_cost_tracking
Create Date: 2026-04-19
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0006_add_call_breakdown"
down_revision = "0005_add_cost_tracking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("intelligence_dossiers") as batch_op:
        batch_op.add_column(sa.Column("call_breakdown", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("intelligence_dossiers") as batch_op:
        batch_op.drop_column("call_breakdown")
