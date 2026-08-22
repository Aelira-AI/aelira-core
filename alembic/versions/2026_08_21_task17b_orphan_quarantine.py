"""Add reviewed orphan quarantine records.

Revision ID: 20260821_task17b_orphan
Revises: 20260821_task17a_jobs
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260821_task17b_orphan"
down_revision = "20260821_task17a_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cloud_webhook_subscriptions",
        sa.Column("provider_resource_id", sa.String(1024), nullable=True),
    )
    op.add_column(
        "cloud_webhook_subscriptions",
        sa.Column("provider_channel_resource_id", sa.String(1024), nullable=True),
    )
    op.add_column(
        "cloud_webhook_subscriptions",
        sa.Column("last_renewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "cloud_webhook_subscriptions",
        sa.Column("renewal_status", sa.String(32), nullable=True),
    )
    op.add_column(
        "cloud_webhook_subscriptions",
        sa.Column("renewal_result", sa.JSON(), nullable=True),
    )
    op.add_column(
        "cloud_webhook_subscriptions",
        sa.Column("pending_renewal_channel_id", sa.String(255), nullable=True),
    )
    op.add_column(
        "cloud_webhook_subscriptions",
        sa.Column(
            "pending_renewal_started_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.create_unique_constraint(
        "uq_cloud_webhook_pending_renewal_channel",
        "cloud_webhook_subscriptions",
        ["pending_renewal_channel_id"],
    )
    op.create_check_constraint(
        "ck_cloud_webhook_pending_renewal_pair",
        "cloud_webhook_subscriptions",
        "(pending_renewal_channel_id IS NULL AND pending_renewal_started_at IS NULL) "
        "OR (pending_renewal_channel_id IS NOT NULL "
        "AND pending_renewal_started_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_cloud_webhook_pending_renewal_status",
        "cloud_webhook_subscriptions",
        "renewal_status NOT IN ('pending', 'requesting', 'indeterminate') "
        "OR (pending_renewal_channel_id IS NOT NULL "
        "AND pending_renewal_started_at IS NOT NULL)",
    )
    op.create_table(
        "maintenance_cursors",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column(
            "cursor_json",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_table(
        "artifact_orphan_quarantine",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("intent_token", sa.String(32), nullable=False),
        sa.Column("original_key", sa.String(1024), nullable=False, unique=True),
        sa.Column("quarantine_key", sa.String(1024), nullable=False, unique=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("source_mtime", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_mtime_ns", sa.BigInteger(), nullable=False),
        sa.Column("source_device", sa.BigInteger(), nullable=False),
        sa.Column("source_inode", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False, server_default="regular_file"),
        sa.Column(
            "status", sa.String(32), nullable=False, server_default="pending_move"
        ),
        sa.Column("reason", sa.String(128), nullable=False),
        sa.Column("recovery_error", sa.String(128), nullable=True),
        sa.Column(
            "quarantined_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", sa.String(255), nullable=True),
        sa.Column("purge_claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purge_token", sa.String(32), nullable=True),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "size_bytes >= 0", name="ck_artifact_orphan_quarantine_size"
        ),
        sa.CheckConstraint(
            "kind IN ('regular_file')",
            name="ck_artifact_orphan_quarantine_kind",
        ),
        sa.CheckConstraint(
            "status IN ('pending_move', 'quarantined', 'restore_required', "
            "'reviewed', 'purging', 'purged')",
            name="ck_artifact_orphan_quarantine_status",
        ),
        sa.CheckConstraint(
            "(purge_claimed_at IS NULL AND purge_token IS NULL) OR "
            "(purge_claimed_at IS NOT NULL AND purge_token IS NOT NULL "
            "AND length(purge_token) = 32)",
            name="ck_artifact_orphan_quarantine_purge_claim",
        ),
        sa.CheckConstraint(
            "(status IN ('pending_move', 'quarantined') "
            "AND reviewed_at IS NULL AND reviewed_by IS NULL "
            "AND purge_claimed_at IS NULL AND purge_token IS NULL "
            "AND purged_at IS NULL) OR "
            "(status = 'restore_required' AND purged_at IS NULL AND "
            "((reviewed_at IS NULL AND reviewed_by IS NULL "
            "AND purge_claimed_at IS NULL AND purge_token IS NULL) OR "
            "(reviewed_at IS NOT NULL AND reviewed_by IS NOT NULL "
            "AND purge_claimed_at IS NOT NULL AND purge_token IS NOT NULL))) OR "
            "(status = 'reviewed' AND reviewed_at IS NOT NULL AND reviewed_by IS NOT NULL "
            "AND purge_claimed_at IS NULL AND purge_token IS NULL "
            "AND purged_at IS NULL) OR "
            "(status = 'purging' AND reviewed_at IS NOT NULL AND reviewed_by IS NOT NULL "
            "AND purge_claimed_at IS NOT NULL AND purge_token IS NOT NULL "
            "AND purged_at IS NULL) OR "
            "(status = 'purged' AND reviewed_at IS NOT NULL AND reviewed_by IS NOT NULL "
            "AND purge_claimed_at IS NOT NULL AND purge_token IS NOT NULL "
            "AND purged_at IS NOT NULL)",
            name="ck_artifact_orphan_quarantine_review",
        ),
    )
    op.create_index(
        "ix_artifact_orphan_quarantine_status_age",
        "artifact_orphan_quarantine",
        ["status", "quarantined_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_artifact_orphan_quarantine_status_age",
        table_name="artifact_orphan_quarantine",
    )
    op.drop_table("artifact_orphan_quarantine")
    op.drop_table("maintenance_cursors")
    op.drop_constraint(
        "ck_cloud_webhook_pending_renewal_status",
        "cloud_webhook_subscriptions",
        type_="check",
    )
    op.drop_constraint(
        "ck_cloud_webhook_pending_renewal_pair",
        "cloud_webhook_subscriptions",
        type_="check",
    )
    op.drop_constraint(
        "uq_cloud_webhook_pending_renewal_channel",
        "cloud_webhook_subscriptions",
        type_="unique",
    )
    for column in (
        "pending_renewal_started_at",
        "pending_renewal_channel_id",
        "renewal_result",
        "renewal_status",
        "last_renewed_at",
        "provider_channel_resource_id",
        "provider_resource_id",
    ):
        op.drop_column("cloud_webhook_subscriptions", column)
