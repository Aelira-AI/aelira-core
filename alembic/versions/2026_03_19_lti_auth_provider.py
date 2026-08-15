"""Add LTI auth provider, User.lti_source, lti_ags_context table

Revision ID: 2026_03_19_lti_auth
Revises: 2026_03_19_provider_metadata
Create Date: 2026-03-19

Adds LTI 1.3 support: enum value for AuthProvider, lti_source column on
users for {issuer}:{lti_user_id} lookups, and lti_ags_context table for
async grade passback via AGS.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

# revision identifiers, used by Alembic.
revision: str = "2026_03_19_lti_auth"
down_revision: Union[str, None] = "2026_03_19_provider_metadata"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def table_exists(table_name: str) -> bool:
    """Check if a table exists (idempotent helper)."""
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    return table_name in inspector.get_table_names()


def column_exists(table_name: str, column_name: str) -> bool:
    """Check if a column exists (idempotent helper)."""
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    columns = [c["name"] for c in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    # 1. Add 'lti' value to authprovider enum.
    #    PostgreSQL enums cannot be altered inside a transaction that also
    #    uses the new value, so we commit first via autocommit.
    #    IF NOT EXISTS makes this idempotent (PostgreSQL 9.3+).
    op.execute("ALTER TYPE authprovider ADD VALUE IF NOT EXISTS 'lti'")

    # 2. Add lti_source column to users table.
    if not column_exists("users", "lti_source"):
        op.add_column(
            "users",
            sa.Column("lti_source", sa.String(255), nullable=True),
        )
        op.create_index("ix_users_lti_source", "users", ["lti_source"])

    # 3. Create lti_ags_context table.
    if not table_exists("lti_ags_context"):
        op.create_table(
            "lti_ags_context",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "department_id",
                sa.String(36),
                sa.ForeignKey("departments.id"),
                nullable=False,
            ),
            sa.Column("course_id", sa.String(255), nullable=False),
            sa.Column("lineitem_url", sa.String(1024), nullable=True),
            sa.Column("token_endpoint", sa.String(1024), nullable=False),
            sa.Column("client_id", sa.String(255), nullable=False),
            sa.Column("scopes", sa.JSON(), default=list),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint(
                "department_id", "course_id", name="uq_ags_dept_course"
            ),
        )


def downgrade() -> None:
    # Drop lti_ags_context table.
    if table_exists("lti_ags_context"):
        op.drop_table("lti_ags_context")

    # Drop lti_source column from users.
    if column_exists("users", "lti_source"):
        op.drop_index("ix_users_lti_source", table_name="users")
        op.drop_column("users", "lti_source")

    # Note: PostgreSQL does not support removing values from an enum type.
    # The 'lti' value will remain in the authprovider enum; this is harmless.
