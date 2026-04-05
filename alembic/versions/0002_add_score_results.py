"""add score results

Revision ID: 0002_add_score_results
Revises: 0001_initial_v2_schema
Create Date: 2026-04-05
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0002_add_score_results"
down_revision = "0001_initial_v2_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "score_results",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("enriched_job_id", sa.String(length=36), sa.ForeignKey("enriched_jobs.id"), nullable=False),
        sa.Column("scoring_version", sa.String(length=64), nullable=False),
        sa.Column("title_relevance_score", sa.Float(), nullable=False),
        sa.Column("location_confidence_score", sa.Float(), nullable=False),
        sa.Column("freshness_confidence_score", sa.Float(), nullable=False),
        sa.Column("source_reliability_score", sa.Float(), nullable=False),
        sa.Column("completeness_score", sa.Float(), nullable=False),
        sa.Column("classification_confidence_score", sa.Float(), nullable=False),
        sa.Column("export_eligibility_score", sa.Float(), nullable=False),
        sa.Column("export_decision", sa.String(length=32), nullable=False),
        sa.Column("reasons", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_score_results_enriched_job_id", "score_results", ["enriched_job_id"], unique=True)
    op.create_index("ix_score_results_scoring_version", "score_results", ["scoring_version"], unique=False)
    op.create_index("ix_score_results_export_eligibility_score", "score_results", ["export_eligibility_score"], unique=False)
    op.create_index("ix_score_results_export_decision", "score_results", ["export_decision"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_score_results_export_decision", table_name="score_results")
    op.drop_index("ix_score_results_export_eligibility_score", table_name="score_results")
    op.drop_index("ix_score_results_scoring_version", table_name="score_results")
    op.drop_index("ix_score_results_enriched_job_id", table_name="score_results")
    op.drop_table("score_results")
