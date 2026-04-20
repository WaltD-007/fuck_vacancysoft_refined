"""add sub_specialism columns to classification_results

Revision ID: 0007_add_sub_specialism
Revises: 0006_add_call_breakdown
Create Date: 2026-04-20

Adds two new columns to classification_results so the per-rule
sub-specialism that the title-taxonomy classifier now returns
(TaxonomyMatch.sub_specialism / sub_specialism_confidence) can be
persisted per lead. Previously sub-specialism was computed downstream
on export only; storing it per-row unlocks DB-side filtering for the
Campaigns page and admin dashboards without backfilling from the
export code path.

Both columns are nullable so the migration is safe against existing
rows. A reclassify pass (prospero pipeline classify --limit 0) will
populate them for rows discovered from now on; older rows stay NULL
until they're reclassified.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0007_add_sub_specialism"
down_revision = "0006_add_call_breakdown"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("classification_results") as batch_op:
        batch_op.add_column(sa.Column("sub_specialism", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("sub_specialism_confidence", sa.Float(), nullable=True))
        batch_op.create_index(
            "ix_classification_results_sub_specialism",
            ["sub_specialism"],
        )


def downgrade() -> None:
    with op.batch_alter_table("classification_results") as batch_op:
        batch_op.drop_index("ix_classification_results_sub_specialism")
        batch_op.drop_column("sub_specialism_confidence")
        batch_op.drop_column("sub_specialism")
