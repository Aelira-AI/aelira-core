"""Add waitlist conversion tracking fields

Revision ID: 2026_01_26_waitlist_conv
Revises: 2026_01_25_campaigns
Create Date: 2026-01-26

Adds fields to track when a waitlist signup converts to a user:
- converted: Boolean flag
- converted_at: Timestamp of conversion
- converted_user_id: FK to users table
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2026_01_26_waitlist_conv'
down_revision: Union[str, None] = '2026_01_25_campaigns'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add conversion tracking fields to waitlist_signups."""

    # Add converted flag
    op.add_column(
        'waitlist_signups',
        sa.Column('converted', sa.Boolean(), server_default='false', nullable=False)
    )

    # Add converted_at timestamp
    op.add_column(
        'waitlist_signups',
        sa.Column('converted_at', sa.DateTime(timezone=True), nullable=True)
    )

    # Add converted_user_id foreign key
    op.add_column(
        'waitlist_signups',
        sa.Column('converted_user_id', sa.String(36), nullable=True)
    )

    # Add foreign key constraint
    op.create_foreign_key(
        'fk_waitlist_converted_user',
        'waitlist_signups',
        'users',
        ['converted_user_id'],
        ['id'],
        ondelete='SET NULL'
    )

    # Add index for finding unconverted waitlist signups
    op.create_index(
        'idx_waitlist_signups_converted',
        'waitlist_signups',
        ['converted']
    )


def downgrade() -> None:
    """Remove conversion tracking fields."""

    op.drop_index('idx_waitlist_signups_converted', 'waitlist_signups')
    op.drop_constraint('fk_waitlist_converted_user', 'waitlist_signups', type_='foreignkey')
    op.drop_column('waitlist_signups', 'converted_user_id')
    op.drop_column('waitlist_signups', 'converted_at')
    op.drop_column('waitlist_signups', 'converted')
