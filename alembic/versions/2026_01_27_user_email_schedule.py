"""Add weekly summary schedule fields to User model

Revision ID: 2026_01_27_email_schedule
Revises: 2026_01_26_waitlist_schema
Create Date: 2026-01-27

Adds weekly_summary_day and weekly_summary_hour columns to users table
so users can choose when they receive their weekly compliance digest.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "2026_01_27_email_schedule"
down_revision: Union[str, None] = "2026_01_26_waitlist_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def column_exists(table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table."""
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [col["name"] for col in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    """Add weekly summary schedule columns to users table."""

    # Add weekly_summary_day if not exists (0=Monday, 6=Sunday)
    if not column_exists("users", "weekly_summary_day"):
        op.add_column(
            "users",
            sa.Column(
                "weekly_summary_day", sa.Integer(), server_default="0", nullable=False
            ),
        )

    # Add weekly_summary_hour if not exists (0-23 UTC)
    if not column_exists("users", "weekly_summary_hour"):
        op.add_column(
            "users",
            sa.Column(
                "weekly_summary_hour", sa.Integer(), server_default="9", nullable=False
            ),
        )


def downgrade() -> None:
    """Remove weekly summary schedule columns."""

    if column_exists("users", "weekly_summary_hour"):
        op.drop_column("users", "weekly_summary_hour")

    if column_exists("users", "weekly_summary_day"):
        op.drop_column("users", "weekly_summary_day")
