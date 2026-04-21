"""add users table

Revision ID: 0009_add_users_table
Revises: 0008_add_outreach_tables
Create Date: 2026-04-21

Adds a ``users`` table — the first step of Prospero's multi-user
story. Schema is intentionally standalone (no FK references from any
other table) so it can be dropped without cascade concerns. Future
PRs may add ``owner_user_id`` columns to sources / campaigns / etc.;
those will be separate migrations.

Columns match src/vacancysoft/db/models.py::User. See the plan at
.claude/plans/linear-meandering-rossum.md for the full design
rationale.

Safe to apply ahead of Entra auth integration — ``entra_object_id`` is
nullable and backfills later via a one-off CLI. Without a user row,
``GET /api/users/me`` returns 401 and the frontend falls back to the
pre-persistence hardcoded defaults.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0009_add_users_table"
down_revision = "0008_add_outreach_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("entra_object_id", sa.String(length=255), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column(
            "role",
            sa.String(length=32),
            nullable=False,
            server_default="operator",
        ),
        sa.Column(
            "active",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("TRUE"),
        ),
        # ``{}`` server-default keeps the JSON-null-vs-empty-dict question
        # settled for any rows inserted outside the ORM (e.g. raw SQL
        # or legacy migration paths). ORM inserts use default=dict.
        sa.Column(
            "preferences",
            sa.JSON,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("last_seen_at", sa.DateTime, nullable=True),
    )
    # Unique indexes for the two lookup keys. entra_object_id is nullable
    # + unique; both Postgres and SQLite permit multiple NULLs in a
    # unique column, which is the behaviour we want pre-Entra.
    op.create_index(
        "ix_users_email", "users", ["email"], unique=True
    )
    op.create_index(
        "ix_users_entra_object_id",
        "users",
        ["entra_object_id"],
        unique=True,
    )
    # Plain index — used by the identity resolver's single-user-mode
    # fallback (SELECT ... WHERE active=true).
    op.create_index("ix_users_active", "users", ["active"])


def downgrade() -> None:
    op.drop_index("ix_users_active", table_name="users")
    op.drop_index("ix_users_entra_object_id", table_name="users")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
