"""add token split and cost_usd to intelligence and campaign tables

Revision ID: 0005_add_cost_tracking
Revises: 0004_add_employment_type
Create Date: 2026-04-19
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0005_add_cost_tracking"
down_revision = "0004_add_employment_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("intelligence_dossiers", "campaign_outputs"):
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(sa.Column("tokens_prompt", sa.Integer(), nullable=True))
            batch_op.add_column(sa.Column("tokens_completion", sa.Integer(), nullable=True))
            batch_op.add_column(sa.Column("cost_usd", sa.Float(), nullable=True))


def downgrade() -> None:
    for table in ("intelligence_dossiers", "campaign_outputs"):
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_column("cost_usd")
            batch_op.drop_column("tokens_completion")
            batch_op.drop_column("tokens_prompt")
