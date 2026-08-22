"""Fence non-idempotent upload requests durably.

Revision ID: 20260822_upload_effect_fence
Revises: 20260821_task17b_reconcile
"""

from alembic import op
import sqlalchemy as sa

revision = "20260822_upload_effect_fence"
down_revision = "20260821_task17b_reconcile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    table = "cloud_job_queue"
    op.add_column(table, sa.Column("external_effect_state", sa.String(20)))
    op.add_column(table, sa.Column("external_effect_token", sa.String(36)))
    op.add_column(
        table, sa.Column("external_effect_started_at", sa.DateTime(timezone=True))
    )
    op.create_check_constraint(
        "ck_cloud_job_queue_external_effect_state",
        table,
        "external_effect_state IS NULL OR external_effect_state IN "
        "('requesting', 'confirmed', 'indeterminate')",
    )
    op.create_check_constraint(
        "ck_cloud_job_queue_external_effect_pair",
        table,
        "(external_effect_state IS NULL AND external_effect_token IS NULL AND "
        "external_effect_started_at IS NULL) OR (external_effect_state IS NOT NULL "
        "AND external_effect_token IS NOT NULL AND external_effect_started_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_cloud_job_queue_external_effect_upload_only",
        table,
        "job_type = 'upload' OR (external_effect_state IS NULL AND "
        "external_effect_token IS NULL AND external_effect_started_at IS NULL)",
    )
    op.create_index(
        "uq_cloud_webhook_initial_intent",
        "cloud_webhook_subscriptions",
        [
            "department_id",
            "credential_id",
            "provider",
            "provider_resource_id",
            "notification_url",
        ],
        unique=True,
        postgresql_where=sa.text(
            "provider = 'google' AND renewal_status IN "
            "('requesting', 'indeterminate', 'created', 'renewed')"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_cloud_webhook_initial_intent",
        table_name="cloud_webhook_subscriptions",
    )
    table = "cloud_job_queue"
    for constraint in (
        "ck_cloud_job_queue_external_effect_upload_only",
        "ck_cloud_job_queue_external_effect_pair",
        "ck_cloud_job_queue_external_effect_state",
    ):
        op.drop_constraint(constraint, table, type_="check")
    for column in (
        "external_effect_started_at",
        "external_effect_token",
        "external_effect_state",
    ):
        op.drop_column(table, column)
