"""initial v2 schema placeholder

Revision ID: 0001_initial_v2_schema
Revises:
Create Date: 2026-04-05
"""

revision = '0001_initial_v2_schema'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create v2 schema from vacancysoft.db.models_v2.BaseV2 metadata.

    This placeholder exists so the Alembic scaffold is checked in.
    The next step is to replace this with the generated full migration.
    """
    pass


def downgrade() -> None:
    pass
