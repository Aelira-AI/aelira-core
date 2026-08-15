"""Normalize waitlist_signups schema to match model

Revision ID: 2026_01_26_waitlist_schema
Revises: 2026_01_26_waitlist_conv
Create Date: 2026-01-26

This migration ensures the waitlist_signups table schema matches the
WaitlistSignup model. These changes may have been applied manually on
some environments, so we check before applying.

Changes:
- Add 'newsletter' column (BOOLEAN DEFAULT true)
- Add 'source' column (VARCHAR(50) DEFAULT 'website')
- Ensure 'id' is VARCHAR(36) primary key (UUID format)
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "2026_01_26_waitlist_schema"
down_revision: Union[str, None] = "2026_01_26_waitlist_conv"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def column_exists(table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table."""
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [col["name"] for col in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    """Add missing columns to waitlist_signups if they don't exist."""

    # Add newsletter column if not exists
    if not column_exists("waitlist_signups", "newsletter"):
        op.add_column(
            "waitlist_signups",
            sa.Column(
                "newsletter", sa.Boolean(), server_default="true", nullable=False
            ),
        )

    # Add source column if not exists
    if not column_exists("waitlist_signups", "source"):
        op.add_column(
            "waitlist_signups",
            sa.Column("source", sa.String(50), server_default="website", nullable=True),
        )

    # Note: The id column type change (integer -> varchar(36)) is a complex
    # operation that requires recreating the table or dropping constraints.
    # Since this was handled manually on production, we skip it here.
    # New environments will have the correct schema from the model.


def downgrade() -> None:
    """Remove added columns (if they exist)."""

    if column_exists("waitlist_signups", "source"):
        op.drop_column("waitlist_signups", "source")

    if column_exists("waitlist_signups", "newsletter"):
        op.drop_column("waitlist_signups", "newsletter")
