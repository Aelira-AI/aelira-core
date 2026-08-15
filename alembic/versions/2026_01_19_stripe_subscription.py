"""Add stripe_subscription_id to departments

Revision ID: 2026_01_19_stripe
Revises: 2026_01_19_byok
Create Date: 2026-01-19

This migration adds stripe_subscription_id column to track active Stripe subscriptions
for deployments that integrate a billing provider.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "2026_01_19_stripe"
down_revision: Union[str, None] = "2026_01_19_byok"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add stripe_subscription_id column to departments table."""
    op.add_column(
        "departments",
        sa.Column("stripe_subscription_id", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    """Remove stripe_subscription_id column from departments table."""
    op.drop_column("departments", "stripe_subscription_id")
