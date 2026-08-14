"""Add marketing campaigns and email tracking tables

Revision ID: 2026_01_25_campaigns
Revises: 2026_01_25_email_prefs
Create Date: 2026-01-25

Adds tables for marketing email campaigns:
- marketing_campaigns: Campaign metadata, content, and aggregate stats
- email_sends: Individual email send records with open/click tracking

Also adds the campaignstatus enum for campaign lifecycle.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2026_01_25_campaigns'
down_revision: Union[str, None] = '2026_01_25_email_prefs'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create marketing campaign and email tracking tables."""

    # Create marketing_campaigns table (sa.Enum auto-creates the campaignstatus type)
    op.create_table(
        'marketing_campaigns',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('subject', sa.String(255), nullable=False),
        sa.Column('preview_text', sa.String(255), nullable=True),
        sa.Column('html_content', sa.Text(), nullable=False),
        sa.Column('plain_content', sa.Text(), nullable=True),
        sa.Column('segment', sa.String(50), server_default='all'),
        sa.Column('audience_filter', sa.JSON(), nullable=True),
        sa.Column(
            'status',
            sa.Enum('draft', 'scheduled', 'sending', 'sent', 'cancelled', name='campaignstatus'),
            server_default='draft',
            nullable=False
        ),
        sa.Column('scheduled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('total_recipients', sa.Integer(), server_default='0'),
        sa.Column('sent_count', sa.Integer(), server_default='0'),
        sa.Column('delivered_count', sa.Integer(), server_default='0'),
        sa.Column('opened_count', sa.Integer(), server_default='0'),
        sa.Column('clicked_count', sa.Integer(), server_default='0'),
        sa.Column('bounced_count', sa.Integer(), server_default='0'),
        sa.Column('unsubscribed_count', sa.Integer(), server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by_id', sa.String(36), sa.ForeignKey('users.id'), nullable=True),
    )

    # Create email_sends table
    op.create_table(
        'email_sends',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('campaign_id', sa.String(36), sa.ForeignKey('marketing_campaigns.id'), nullable=False),
        sa.Column('recipient_email', sa.String(255), nullable=False),
        sa.Column('recipient_name', sa.String(255), nullable=True),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('tracking_token', sa.String(64), nullable=False, unique=True),
        sa.Column('ses_message_id', sa.String(255), nullable=True),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('bounced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('bounce_type', sa.String(50), nullable=True),
        sa.Column('opened_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('open_count', sa.Integer(), server_default='0'),
        sa.Column('clicked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('click_count', sa.Integer(), server_default='0'),
        sa.Column('unsubscribed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Create indexes for marketing_campaigns
    op.create_index(
        'idx_marketing_campaigns_status',
        'marketing_campaigns',
        ['status']
    )
    op.create_index(
        'idx_marketing_campaigns_scheduled_at',
        'marketing_campaigns',
        ['scheduled_at']
    )
    op.create_index(
        'idx_marketing_campaigns_created',
        'marketing_campaigns',
        [sa.text('created_at DESC')]
    )

    # Create indexes for email_sends
    op.create_index(
        'idx_email_sends_campaign_id',
        'email_sends',
        ['campaign_id']
    )
    op.create_index(
        'idx_email_sends_tracking_token',
        'email_sends',
        ['tracking_token']
    )
    op.create_index(
        'idx_email_sends_recipient_email',
        'email_sends',
        ['recipient_email']
    )
    op.create_index(
        'idx_email_sends_ses_message_id',
        'email_sends',
        ['ses_message_id']
    )
    op.create_index(
        'idx_email_sends_campaign_sent',
        'email_sends',
        ['campaign_id', sa.text('sent_at DESC')]
    )


def downgrade() -> None:
    """Drop marketing campaign and email tracking tables."""

    # Drop indexes
    op.drop_index('idx_email_sends_campaign_sent', 'email_sends')
    op.drop_index('idx_email_sends_ses_message_id', 'email_sends')
    op.drop_index('idx_email_sends_recipient_email', 'email_sends')
    op.drop_index('idx_email_sends_tracking_token', 'email_sends')
    op.drop_index('idx_email_sends_campaign_id', 'email_sends')
    op.drop_index('idx_marketing_campaigns_created', 'marketing_campaigns')
    op.drop_index('idx_marketing_campaigns_scheduled_at', 'marketing_campaigns')
    op.drop_index('idx_marketing_campaigns_status', 'marketing_campaigns')

    # Drop tables
    op.drop_table('email_sends')
    op.drop_table('marketing_campaigns')

    # Drop enum
    sa.Enum(name='campaignstatus').drop(op.get_bind(), checkfirst=True)
