"""initial v2 schema

Revision ID: 0001_initial_v2_schema
Revises:
Create Date: 2026-04-05
"""

from __future__ import annotations

from alembic import op

from vacancysoft.db.models import Base

revision = "0001_initial_v2_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
