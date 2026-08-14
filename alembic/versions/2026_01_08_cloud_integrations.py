"""Add cloud integration tables for Google Workspace, Microsoft 365, and LMS integrations

Revision ID: 2026_01_08_cloud_integrations
Revises: 2026_01_08_lead_tracking
Create Date: 2026-01-08 14:00:00

Tables created:
- cloud_oauth_credentials: OAuth tokens per department for Google/Microsoft
- cloud_files: Files tracked from cloud storage
- cloud_webhook_subscriptions: Webhook subscriptions for real-time updates
- cloud_job_queue: Background job queue for cloud operations
- email_alert_settings: Email notification preferences
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2026_01_08_cloud_integrations'
down_revision: Union[str, None] = '2026_01_08_lead_tracking'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create cloud integration tables."""

    # 1. Cloud OAuth Credentials - stores encrypted OAuth tokens per department
    op.create_table(
        'cloud_oauth_credentials',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('department_id', sa.String(36), sa.ForeignKey('departments.id', ondelete='CASCADE'), nullable=False),

        # Provider type: google, microsoft
        sa.Column('provider', sa.String(20), nullable=False),

        # OAuth tokens (encrypted at rest in application layer)
        sa.Column('access_token', sa.Text, nullable=False),
        sa.Column('refresh_token', sa.Text, nullable=False),
        sa.Column('token_expires_at', sa.DateTime(timezone=True), nullable=False),

        # User info from OAuth provider
        sa.Column('provider_user_id', sa.String(255), nullable=True),
        sa.Column('provider_email', sa.String(255), nullable=True),
        sa.Column('provider_name', sa.String(255), nullable=True),

        # Scopes granted (stored as JSON array)
        sa.Column('scopes', sa.JSON, nullable=True),

        # Connection state
        sa.Column('is_active', sa.Boolean, server_default='true', nullable=False),
        sa.Column('last_sync_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error', sa.Text, nullable=True),

        # Timestamps
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), onupdate=sa.func.now(), nullable=True),

        # Unique constraint: one connection per provider per department
        sa.UniqueConstraint('department_id', 'provider', name='uq_cloud_credentials_dept_provider'),
    )

    # Indexes for cloud_oauth_credentials
    op.create_index('ix_cloud_oauth_credentials_department', 'cloud_oauth_credentials', ['department_id'])
    op.create_index('ix_cloud_oauth_credentials_provider', 'cloud_oauth_credentials', ['provider'])

    # 2. Cloud Files - tracks files from cloud storage for scanning
    op.create_table(
        'cloud_files',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('department_id', sa.String(36), sa.ForeignKey('departments.id', ondelete='CASCADE'), nullable=False),
        sa.Column('credential_id', sa.String(36), sa.ForeignKey('cloud_oauth_credentials.id', ondelete='CASCADE'), nullable=False),

        # Provider-specific IDs
        sa.Column('provider', sa.String(20), nullable=False),  # google, microsoft
        sa.Column('provider_file_id', sa.String(255), nullable=False),  # Google Drive ID / OneDrive ID
        sa.Column('provider_parent_id', sa.String(255), nullable=True),  # Folder/drive ID

        # File metadata
        sa.Column('file_name', sa.String(512), nullable=False),
        sa.Column('file_type', sa.String(20), nullable=False),  # docx, pptx, xlsx, pdf, gdoc, gslide, gsheet
        sa.Column('mime_type', sa.String(100), nullable=True),
        sa.Column('file_size_bytes', sa.BigInteger, nullable=True),
        sa.Column('file_hash', sa.String(64), nullable=True),  # For change detection

        # Provider metadata
        sa.Column('web_view_link', sa.String(1024), nullable=True),
        sa.Column('download_link', sa.String(1024), nullable=True),

        # Version tracking
        sa.Column('provider_version', sa.String(100), nullable=True),  # etag/version for change detection
        sa.Column('provider_modified_at', sa.DateTime(timezone=True), nullable=True),

        # Scan state
        sa.Column('last_scan_id', sa.String(36), sa.ForeignKey('scans.id', ondelete='SET NULL'), nullable=True),
        sa.Column('last_scanned_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_compliance_score', sa.Float, nullable=True),
        sa.Column('needs_rescan', sa.Boolean, server_default='true', nullable=False),

        # Remediation state
        sa.Column('has_remediated_version', sa.Boolean, server_default='false', nullable=False),
        sa.Column('remediated_file_id', sa.String(255), nullable=True),  # ID of fixed file if uploaded

        # Timestamps
        sa.Column('discovered_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), onupdate=sa.func.now(), nullable=True),

        # Unique constraint: one record per file per department
        sa.UniqueConstraint('department_id', 'provider', 'provider_file_id', name='uq_cloud_files_dept_provider_file'),
    )

    # Indexes for cloud_files
    op.create_index('ix_cloud_files_department', 'cloud_files', ['department_id'])
    op.create_index('ix_cloud_files_credential', 'cloud_files', ['credential_id'])
    op.create_index('ix_cloud_files_needs_rescan', 'cloud_files', ['department_id', 'needs_rescan'])
    op.create_index('ix_cloud_files_provider_file', 'cloud_files', ['provider', 'provider_file_id'])

    # 3. Cloud Webhook Subscriptions - for real-time file change notifications
    op.create_table(
        'cloud_webhook_subscriptions',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('department_id', sa.String(36), sa.ForeignKey('departments.id', ondelete='CASCADE'), nullable=False),
        sa.Column('credential_id', sa.String(36), sa.ForeignKey('cloud_oauth_credentials.id', ondelete='CASCADE'), nullable=False),

        sa.Column('provider', sa.String(20), nullable=False),
        sa.Column('subscription_id', sa.String(255), nullable=False),  # Provider's subscription/channel ID
        sa.Column('resource_uri', sa.String(1024), nullable=True),  # What we're watching (folder, drive, etc.)

        # Subscription details
        sa.Column('expiration_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('notification_url', sa.String(1024), nullable=True),

        # State
        sa.Column('is_active', sa.Boolean, server_default='true', nullable=False),
        sa.Column('last_notification_at', sa.DateTime(timezone=True), nullable=True),

        # Timestamps
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # Indexes for cloud_webhook_subscriptions
    op.create_index('ix_cloud_webhooks_credential', 'cloud_webhook_subscriptions', ['credential_id'])
    op.create_index('ix_cloud_webhooks_active', 'cloud_webhook_subscriptions', ['provider', 'is_active'])
    op.create_index('ix_cloud_webhooks_expiration', 'cloud_webhook_subscriptions', ['expiration_time'])

    # 4. Cloud Job Queue - background job queue for cloud operations
    op.create_table(
        'cloud_job_queue',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('department_id', sa.String(36), sa.ForeignKey('departments.id', ondelete='CASCADE'), nullable=False),

        # Job type: sync, scan, remediate, upload, webhook_refresh
        sa.Column('job_type', sa.String(50), nullable=False),

        # Job target
        sa.Column('cloud_file_id', sa.String(36), sa.ForeignKey('cloud_files.id', ondelete='CASCADE'), nullable=True),
        sa.Column('credential_id', sa.String(36), sa.ForeignKey('cloud_oauth_credentials.id', ondelete='CASCADE'), nullable=True),
        sa.Column('provider', sa.String(20), nullable=True),
        sa.Column('provider_file_id', sa.String(255), nullable=True),

        # Job state
        sa.Column('status', sa.String(20), server_default='pending', nullable=False),  # pending, processing, completed, failed
        sa.Column('priority', sa.Integer, server_default='5', nullable=False),  # 1=highest, 10=lowest

        # Progress
        sa.Column('progress', sa.Integer, server_default='0', nullable=False),
        sa.Column('progress_message', sa.Text, nullable=True),

        # Results
        sa.Column('result_data', sa.JSON, nullable=True),
        sa.Column('error_message', sa.Text, nullable=True),
        sa.Column('retry_count', sa.Integer, server_default='0', nullable=False),
        sa.Column('max_retries', sa.Integer, server_default='3', nullable=False),

        # Timing
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('scheduled_for', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # Indexes for cloud_job_queue
    op.create_index('ix_cloud_jobs_pending', 'cloud_job_queue', ['status', 'priority', 'scheduled_for'])
    op.create_index('ix_cloud_jobs_department', 'cloud_job_queue', ['department_id'])
    op.create_index('ix_cloud_jobs_file', 'cloud_job_queue', ['cloud_file_id'])

    # 5. Email Alert Settings - notification preferences per department/user
    op.create_table(
        'email_alert_settings',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('department_id', sa.String(36), sa.ForeignKey('departments.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=True),  # NULL = department-wide

        # Alert types
        sa.Column('alert_on_scan_complete', sa.Boolean, server_default='true', nullable=False),
        sa.Column('alert_on_new_issues', sa.Boolean, server_default='true', nullable=False),
        sa.Column('alert_on_critical_issues', sa.Boolean, server_default='true', nullable=False),
        sa.Column('alert_weekly_summary', sa.Boolean, server_default='true', nullable=False),

        # Delivery preferences
        sa.Column('email_addresses', sa.JSON, nullable=True),  # Array of email addresses
        sa.Column('min_severity', sa.String(20), server_default='medium', nullable=False),  # Only alert >= this severity

        # Quiet hours (optional)
        sa.Column('quiet_hours_start', sa.Time, nullable=True),
        sa.Column('quiet_hours_end', sa.Time, nullable=True),
        sa.Column('timezone', sa.String(50), server_default='America/New_York', nullable=False),

        # Timestamps
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), onupdate=sa.func.now(), nullable=True),

        # Unique constraint: one setting per user per department (or one department-wide setting)
        sa.UniqueConstraint('department_id', 'user_id', name='uq_email_alerts_dept_user'),
    )

    # Indexes for email_alert_settings
    op.create_index('ix_email_alerts_department', 'email_alert_settings', ['department_id'])


def downgrade() -> None:
    """Drop cloud integration tables."""
    # Drop in reverse order due to foreign key dependencies

    # Drop email_alert_settings
    op.drop_index('ix_email_alerts_department', table_name='email_alert_settings')
    op.drop_table('email_alert_settings')

    # Drop cloud_job_queue
    op.drop_index('ix_cloud_jobs_file', table_name='cloud_job_queue')
    op.drop_index('ix_cloud_jobs_department', table_name='cloud_job_queue')
    op.drop_index('ix_cloud_jobs_pending', table_name='cloud_job_queue')
    op.drop_table('cloud_job_queue')

    # Drop cloud_webhook_subscriptions
    op.drop_index('ix_cloud_webhooks_expiration', table_name='cloud_webhook_subscriptions')
    op.drop_index('ix_cloud_webhooks_active', table_name='cloud_webhook_subscriptions')
    op.drop_index('ix_cloud_webhooks_credential', table_name='cloud_webhook_subscriptions')
    op.drop_table('cloud_webhook_subscriptions')

    # Drop cloud_files
    op.drop_index('ix_cloud_files_provider_file', table_name='cloud_files')
    op.drop_index('ix_cloud_files_needs_rescan', table_name='cloud_files')
    op.drop_index('ix_cloud_files_credential', table_name='cloud_files')
    op.drop_index('ix_cloud_files_department', table_name='cloud_files')
    op.drop_table('cloud_files')

    # Drop cloud_oauth_credentials
    op.drop_index('ix_cloud_oauth_credentials_provider', table_name='cloud_oauth_credentials')
    op.drop_index('ix_cloud_oauth_credentials_department', table_name='cloud_oauth_credentials')
    op.drop_table('cloud_oauth_credentials')
