"""Fix api_keys rate_limit_per_hour default value

Revision ID: fix_api_keys_defaults
Revises: 2026_01_18_email_unsubscribe
Create Date: 2026-01-18

This migration:
1. Sets a server default of 1000 for rate_limit_per_hour
2. Updates any existing NULL values to 1000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "fix_api_keys_defaults"
down_revision = "2026_01_18_unsubscribe"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # First, update any existing NULL values
    op.execute("""
        UPDATE api_keys
        SET rate_limit_per_hour = 1000
        WHERE rate_limit_per_hour IS NULL
        """)

    # Add server default for future inserts
    op.alter_column(
        "api_keys",
        "rate_limit_per_hour",
        existing_type=sa.Integer(),
        server_default="1000",
        existing_nullable=True,
    )


def downgrade() -> None:
    # Remove server default
    op.alter_column(
        "api_keys",
        "rate_limit_per_hour",
        existing_type=sa.Integer(),
        server_default=None,
        existing_nullable=True,
    )
