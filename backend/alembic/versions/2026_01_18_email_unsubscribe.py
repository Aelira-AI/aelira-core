"""add email unsubscribe fields to waitlist_signups

Revision ID: 2026_01_18_unsubscribe
Revises: 2026_01_16_lti_reg
Create Date: 2026-01-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "2026_01_18_unsubscribe"
down_revision: Union[str, None] = "2026_01_16_lti_reg"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add unsubscribe fields to waitlist_signups table
    op.add_column(
        "waitlist_signups",
        sa.Column("unsubscribed", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "waitlist_signups",
        sa.Column("unsubscribed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "waitlist_signups",
        sa.Column("unsubscribe_token", sa.String(64), nullable=True, unique=True),
    )

    # Create index on unsubscribe_token for fast lookups
    op.create_index(
        "ix_waitlist_signups_unsubscribe_token",
        "waitlist_signups",
        ["unsubscribe_token"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_waitlist_signups_unsubscribe_token", table_name="waitlist_signups")
    op.drop_column("waitlist_signups", "unsubscribe_token")
    op.drop_column("waitlist_signups", "unsubscribed_at")
    op.drop_column("waitlist_signups", "unsubscribed")
