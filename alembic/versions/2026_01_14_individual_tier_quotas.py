"""Add individual_free tier and quota tracking fields

Revision ID: 2026_01_14_individual
Revises: 2026_01_13_merge_heads
Create Date: 2026-01-14

This migration adds support for the individual faculty free tier:
- Adds quota tracking columns to departments table
- Sets unlimited quotas (-1) for existing departments
"""

from alembic import op
import sqlalchemy as sa
from datetime import datetime, timezone

# revision identifiers, used by Alembic.
revision = "2026_01_14_individual"
down_revision = "2026_01_13_merge_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add quota tracking columns to departments table."""
    # Add scans_this_month column
    op.add_column(
        "departments",
        sa.Column("scans_this_month", sa.Integer(), nullable=True, default=0),
    )

    # Add pages_this_month column
    op.add_column(
        "departments",
        sa.Column("pages_this_month", sa.Integer(), nullable=True, default=0),
    )

    # Add quota_reset_at column
    op.add_column(
        "departments",
        sa.Column("quota_reset_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Set default values for existing rows
    # Existing departments get unlimited quotas (represented by 0, not -1, since these are cumulative)
    # They don't need quota tracking since they're on paid plans
    op.execute("""
        UPDATE departments
        SET scans_this_month = 0,
            pages_this_month = 0,
            quota_reset_at = NULL
        WHERE scans_this_month IS NULL
        """)

    # Make columns non-nullable after setting defaults
    op.alter_column(
        "departments", "scans_this_month", nullable=False, server_default="0"
    )
    op.alter_column(
        "departments", "pages_this_month", nullable=False, server_default="0"
    )


def downgrade() -> None:
    """Remove quota tracking columns from departments table."""
    op.drop_column("departments", "quota_reset_at")
    op.drop_column("departments", "pages_this_month")
    op.drop_column("departments", "scans_this_month")
