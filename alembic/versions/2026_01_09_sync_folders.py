"""Add cloud_sync_folders table

Revision ID: 2026_01_09_sync_folders
Revises: 2026_01_08_cloud_integrations
Create Date: 2026-01-09 01:05:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '2026_01_09_sync_folders'
down_revision = '2026_01_08_cloud_integrations'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create cloud_sync_folders table
    op.create_table(
        'cloud_sync_folders',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('department_id', sa.String(36), sa.ForeignKey('departments.id', ondelete='CASCADE'), nullable=False),
        sa.Column('credential_id', sa.String(36), sa.ForeignKey('cloud_oauth_credentials.id', ondelete='CASCADE'), nullable=False),
        sa.Column('provider', sa.String(20), nullable=False),  # google, microsoft
        sa.Column('provider_folder_id', sa.String(255), nullable=False),  # Drive folder ID / OneDrive folder ID
        sa.Column('folder_name', sa.String(512), nullable=False),
        sa.Column('folder_path', sa.Text, nullable=True),  # Human-readable path
        sa.Column('is_active', sa.Boolean, default=True, nullable=False),
        sa.Column('sync_subfolders', sa.Boolean, default=True, nullable=False),  # Sync all subfolders
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), onupdate=sa.func.now()),
    )

    # Create indexes
    op.create_index('ix_cloud_sync_folders_department_id', 'cloud_sync_folders', ['department_id'])
    op.create_index('ix_cloud_sync_folders_credential_id', 'cloud_sync_folders', ['credential_id'])
    op.create_index(
        'ix_cloud_sync_folders_provider_folder',
        'cloud_sync_folders',
        ['provider', 'provider_folder_id'],
    )

    # Ensure unique folder per credential
    op.create_unique_constraint(
        'uq_cloud_sync_folders_credential_folder',
        'cloud_sync_folders',
        ['credential_id', 'provider_folder_id'],
    )


def downgrade() -> None:
    op.drop_table('cloud_sync_folders')
