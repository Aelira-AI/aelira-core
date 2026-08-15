"""Add images_this_month column to departments for image quota tracking

Revision ID: 2026_01_19_image
Revises: 2026_01_19_audit
Create Date: 2026-01-19

This migration adds images_this_month column to track standalone image API usage
separately from document scan quotas. This allows a more generous image limit
(20/month for free tier) while keeping document scans limited (10/month).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "2026_01_19_image"
down_revision: Union[str, None] = "2026_01_19_audit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add images_this_month column to departments table."""
    # Add images_this_month column
    op.add_column(
        "departments",
        sa.Column("images_this_month", sa.Integer(), nullable=True, default=0),
    )

    # Set default values for existing rows
    op.execute("""
        UPDATE departments
        SET images_this_month = 0
        WHERE images_this_month IS NULL
        """)

    # Make column non-nullable after setting defaults
    op.alter_column(
        "departments", "images_this_month", nullable=False, server_default="0"
    )


def downgrade() -> None:
    """Remove images_this_month column from departments table."""
    op.drop_column("departments", "images_this_month")
