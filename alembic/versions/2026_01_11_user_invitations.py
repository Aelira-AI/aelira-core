"""Add user_invitations table for faculty invitation system

Revision ID: 2026_01_11_user_invitations
Revises: 2026_01_09_sync_folders
Create Date: 2026-01-11 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '2026_01_11_user_invitations'
down_revision = '1807727d838d'  # Changed from 2026_01_09_sync_folders to make chain linear
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create invitation_status enum only if it doesn't exist
    conn = op.get_bind()
    result = conn.execute(
        sa.text("SELECT 1 FROM pg_type WHERE typname = 'invitationstatus'")
    )
    if not result.fetchone():
        # Create the enum type via raw SQL
        conn.execute(
            sa.text("CREATE TYPE invitationstatus AS ENUM ('pending', 'accepted', 'expired', 'revoked')")
        )

    # Reference existing enum types without creating them
    # NOTE: userrole was created with UPPERCASE values in initial migration
    invitation_status_enum = postgresql.ENUM(
        'pending', 'accepted', 'expired', 'revoked',
        name='invitationstatus',
        create_type=False
    )
    user_role_enum = postgresql.ENUM(
        'FACULTY', 'ADMIN', 'SUPER_ADMIN',
        name='userrole',
        create_type=False
    )

    # Create user_invitations table
    op.create_table(
        'user_invitations',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('department_id', sa.String(36), sa.ForeignKey('departments.id', ondelete='CASCADE'), nullable=False),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('role', user_role_enum, server_default='FACULTY'),
        sa.Column('token', sa.String(64), unique=True, nullable=False),
        sa.Column('invited_by', sa.String(36), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('status', invitation_status_enum, server_default='pending'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    )

    # Create indexes
    op.create_index('idx_user_invitations_token', 'user_invitations', ['token'])
    op.create_index('idx_user_invitations_department_status', 'user_invitations', ['department_id', 'status'])
    op.create_index('idx_user_invitations_email', 'user_invitations', ['email'])


def downgrade() -> None:
    op.drop_table('user_invitations')

    # Drop the enum type
    invitation_status = sa.Enum(name='invitationstatus')
    invitation_status.drop(op.get_bind(), checkfirst=True)
