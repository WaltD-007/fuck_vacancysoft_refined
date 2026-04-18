"""add classification employment_type

Revision ID: 0004_add_employment_type
Revises: 0003_add_intelligence_tables
Create Date: 2026-04-19
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0004_add_employment_type"
down_revision = "0003_add_intelligence_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("classification_results") as batch_op:
        batch_op.add_column(
            sa.Column(
                "employment_type",
                sa.String(length=32),
                nullable=False,
                server_default="Permanent",
            )
        )
    op.create_index(
        "ix_classification_results_employment_type",
        "classification_results",
        ["employment_type"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_classification_results_employment_type",
        table_name="classification_results",
    )
    with op.batch_alter_table("classification_results") as batch_op:
        batch_op.drop_column("employment_type")
