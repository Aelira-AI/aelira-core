"""Add account deletion support

Revision ID: 2026_01_28_account_deletion
Revises: 2026_01_27_magic_link_signup
Create Date: 2026-01-28

Creates deleted_emails table for re-registration blocking via SHA-256 email
hashes, and adds deletion scheduling fields to the users table.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = '2026_01_28_account_deletion'
down_revision: Union[str, None] = '2026_01_27_magic_link_signup'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def table_exists(table_name: str) -> bool:
    """Check if a table exists."""
    bind = op.get_bind()
    inspector = inspect(bind)
    return table_name in inspector.get_table_names()


def column_exists(table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table."""
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [col['name'] for col in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    # Create deleted_emails table
    if not table_exists('deleted_emails'):
        op.create_table(
            'deleted_emails',
            sa.Column('id', sa.String(36), primary_key=True),
            sa.Column('email_hash', sa.String(64), unique=True, nullable=False),
            sa.Column('deletion_type', sa.String(20), nullable=False),
            sa.Column('deleted_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column('cooldown_until', sa.DateTime(timezone=True), nullable=True),
            sa.Column('previous_tier', sa.String(50), nullable=True),
            sa.Column('reason', sa.Text(), nullable=True),
        )
        op.create_index(
            'ix_deleted_emails_email_hash',
            'deleted_emails',
            ['email_hash'],
            unique=True,
        )

    # Add deletion fields to users table
    user_columns = [
        ('deactivated_at', sa.DateTime(timezone=True)),
        ('deletion_requested_at', sa.DateTime(timezone=True)),
        ('deletion_scheduled_for', sa.DateTime(timezone=True)),
        ('deletion_confirmation_code_hash', sa.String(255)),
        ('deletion_confirmation_expires_at', sa.DateTime(timezone=True)),
    ]

    for col_name, col_type in user_columns:
        if not column_exists('users', col_name):
            op.add_column(
                'users',
                sa.Column(col_name, col_type, nullable=True),
            )


def downgrade() -> None:
    # Remove user deletion columns
    user_columns = [
        'deletion_confirmation_expires_at',
        'deletion_confirmation_code_hash',
        'deletion_scheduled_for',
        'deletion_requested_at',
        'deactivated_at',
    ]

    for col_name in user_columns:
        if column_exists('users', col_name):
            op.drop_column('users', col_name)

    # Drop deleted_emails table
    if table_exists('deleted_emails'):
        op.drop_index('ix_deleted_emails_email_hash', table_name='deleted_emails')
        op.drop_table('deleted_emails')
