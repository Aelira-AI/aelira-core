"""Add double opt-in email confirmation fields

Revision ID: 2026_01_27_double_opt_in
Revises: 2026_01_27_email_schedule
Create Date: 2026-01-27

Adds email confirmation fields to waitlist_signups and users tables
for double opt-in compliance (required for AWS SES production access).

WaitlistSignup: email_confirmed, email_confirmation_token, email_confirmed_at
User: email_marketing_confirmed, email_marketing_confirmation_token, email_marketing_confirmed_at
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = '2026_01_27_double_opt_in'
down_revision: Union[str, None] = '2026_01_27_email_schedule'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def column_exists(table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table."""
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [col['name'] for col in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    """Add double opt-in confirmation fields."""

    # --- waitlist_signups table ---

    if not column_exists('waitlist_signups', 'email_confirmed'):
        op.add_column(
            'waitlist_signups',
            sa.Column('email_confirmed', sa.Boolean(), server_default='false', nullable=False)
        )

    if not column_exists('waitlist_signups', 'email_confirmation_token'):
        op.add_column(
            'waitlist_signups',
            sa.Column('email_confirmation_token', sa.String(64), nullable=True)
        )
        op.create_index(
            'ix_waitlist_signups_email_confirmation_token',
            'waitlist_signups',
            ['email_confirmation_token'],
            unique=True
        )

    if not column_exists('waitlist_signups', 'email_confirmed_at'):
        op.add_column(
            'waitlist_signups',
            sa.Column('email_confirmed_at', sa.DateTime(timezone=True), nullable=True)
        )

    # --- users table ---

    if not column_exists('users', 'email_marketing_confirmed'):
        op.add_column(
            'users',
            sa.Column('email_marketing_confirmed', sa.Boolean(), server_default='false', nullable=False)
        )

    if not column_exists('users', 'email_marketing_confirmation_token'):
        op.add_column(
            'users',
            sa.Column('email_marketing_confirmation_token', sa.String(64), nullable=True)
        )
        op.create_index(
            'ix_users_email_marketing_confirmation_token',
            'users',
            ['email_marketing_confirmation_token'],
            unique=True
        )

    if not column_exists('users', 'email_marketing_confirmed_at'):
        op.add_column(
            'users',
            sa.Column('email_marketing_confirmed_at', sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    """Remove double opt-in confirmation fields."""

    # --- users table ---
    if column_exists('users', 'email_marketing_confirmed_at'):
        op.drop_column('users', 'email_marketing_confirmed_at')

    if column_exists('users', 'email_marketing_confirmation_token'):
        op.drop_index('ix_users_email_marketing_confirmation_token', table_name='users')
        op.drop_column('users', 'email_marketing_confirmation_token')

    if column_exists('users', 'email_marketing_confirmed'):
        op.drop_column('users', 'email_marketing_confirmed')

    # --- waitlist_signups table ---
    if column_exists('waitlist_signups', 'email_confirmed_at'):
        op.drop_column('waitlist_signups', 'email_confirmed_at')

    if column_exists('waitlist_signups', 'email_confirmation_token'):
        op.drop_index('ix_waitlist_signups_email_confirmation_token', table_name='waitlist_signups')
        op.drop_column('waitlist_signups', 'email_confirmation_token')

    if column_exists('waitlist_signups', 'email_confirmed'):
        op.drop_column('waitlist_signups', 'email_confirmed')
