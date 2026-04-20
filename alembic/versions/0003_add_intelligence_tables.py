"""add intelligence and campaign tables

Revision ID: 0003_add_intelligence_tables
Revises: 0002_add_score_results
Create Date: 2026-04-18
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0003_add_intelligence_tables"
down_revision = "0002_add_score_results"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "intelligence_dossiers",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("enriched_job_id", sa.String(length=36), sa.ForeignKey("enriched_jobs.id"), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("category_used", sa.String(length=128), nullable=False),
        sa.Column("model_used", sa.String(length=128), nullable=False),
        sa.Column("company_context", sa.Text(), nullable=True),
        sa.Column("core_problem", sa.Text(), nullable=True),
        sa.Column("stated_vs_actual", sa.JSON(), nullable=True),
        sa.Column("spec_risk", sa.JSON(), nullable=True),
        sa.Column("candidate_profiles", sa.JSON(), nullable=True),
        sa.Column("search_booleans", sa.JSON(), nullable=True),
        sa.Column("lead_score", sa.Float(), nullable=True),
        sa.Column("lead_score_justification", sa.Text(), nullable=True),
        sa.Column("hiring_managers", sa.JSON(), nullable=True),
        sa.Column("raw_response", sa.JSON(), nullable=True),
        sa.Column("tokens_used", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_intelligence_dossiers_enriched_job_id",
        "intelligence_dossiers",
        ["enriched_job_id"],
        unique=False,
    )
    op.create_index(
        "ix_intelligence_dossiers_category_used",
        "intelligence_dossiers",
        ["category_used"],
        unique=False,
    )

    op.create_table(
        "campaign_outputs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("dossier_id", sa.String(length=36), sa.ForeignKey("intelligence_dossiers.id"), nullable=False),
        sa.Column("model_used", sa.String(length=128), nullable=False),
        sa.Column("outreach_emails", sa.JSON(), nullable=True),
        sa.Column("raw_response", sa.JSON(), nullable=True),
        sa.Column("tokens_used", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_campaign_outputs_dossier_id",
        "campaign_outputs",
        ["dossier_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_campaign_outputs_dossier_id", table_name="campaign_outputs")
    op.drop_table("campaign_outputs")
    op.drop_index("ix_intelligence_dossiers_category_used", table_name="intelligence_dossiers")
    op.drop_index("ix_intelligence_dossiers_enriched_job_id", table_name="intelligence_dossiers")
    op.drop_table("intelligence_dossiers")
