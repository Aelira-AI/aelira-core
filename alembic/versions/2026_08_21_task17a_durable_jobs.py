"""Add atomic durable queue claims and worker heartbeats.

Revision ID: 20260821_task17a_jobs
Revises: 20260821_task16b2_review
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260821_task17a_jobs"
down_revision = "20260821_task16b2_review"
branch_labels = None
depends_on = None

JOB = "cloud_job_queue"


def upgrade():
    op.add_column(
        JOB,
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(JOB, sa.Column("depends_on_job_id", sa.String(36), nullable=True))
    op.add_column(JOB, sa.Column("dedupe_key", sa.String(255), nullable=True))
    op.add_column(
        JOB, sa.Column("attempt_count", sa.Integer(), nullable=True, server_default="0")
    )
    op.add_column(JOB, sa.Column("claim_token", sa.String(36), nullable=True))
    op.add_column(JOB, sa.Column("worker_id", sa.String(255), nullable=True))
    op.add_column(
        JOB, sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        JOB, sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        JOB, sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(JOB, sa.Column("last_error_code", sa.String(128), nullable=True))
    op.add_column(JOB, sa.Column("last_error_retryable", sa.Boolean(), nullable=True))
    op.add_column(
        JOB,
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
    )

    # Preserve result_data. It historically carried both input and output; only copy
    # object-shaped data for not-yet-run jobs whose new payload is still empty.
    op.execute(sa.text("""
        UPDATE cloud_job_queue
           SET payload = result_data::jsonb
         WHERE status = 'pending'
           AND result_data IS NOT NULL
           AND jsonb_typeof(result_data::jsonb) = 'object'
           AND payload = '{}'::jsonb
    """))
    # Legacy processing rows have no ownership proof. Make them safely claimable.
    op.execute(sa.text("""
        UPDATE cloud_job_queue
           SET status = 'pending', scheduled_for = now(), progress = 0,
               completed_at = NULL, attempt_count = COALESCE(retry_count, 0)
         WHERE status = 'processing'
    """))
    op.execute(sa.text("""
        UPDATE cloud_job_queue
           SET completed_at = COALESCE(completed_at, started_at, created_at, now())
         WHERE status IN ('completed', 'failed')
    """))
    # Legacy schemas allowed these columns to be nullable. Backfill every row
    # before applying NOT NULL so upgrades cannot strand existing queues.
    op.execute(sa.text("""
        UPDATE cloud_job_queue
           SET status = COALESCE(status, 'pending'),
               priority = COALESCE(priority, 5),
               progress = COALESCE(progress, 0),
               retry_count = COALESCE(retry_count, 0),
               max_retries = COALESCE(max_retries, 3),
               scheduled_for = COALESCE(scheduled_for, created_at, now()),
               payload = COALESCE(payload, '{}'::jsonb)
    """))
    op.alter_column(JOB, "payload", nullable=False)
    op.alter_column(JOB, "attempt_count", nullable=False)
    op.alter_column(JOB, "updated_at", nullable=False)
    op.alter_column(JOB, "status", nullable=False, server_default="pending")
    op.alter_column(JOB, "priority", nullable=False, server_default="5")
    op.alter_column(JOB, "progress", nullable=False, server_default="0")
    op.alter_column(JOB, "retry_count", nullable=False, server_default="0")
    op.alter_column(JOB, "max_retries", nullable=False, server_default="3")
    op.alter_column(JOB, "scheduled_for", nullable=False)

    op.create_foreign_key(
        "fk_cloud_job_queue_dependency",
        JOB,
        JOB,
        ["depends_on_job_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_cloud_job_queue_status",
        JOB,
        "status IN ('pending', 'processing', 'completed', 'failed')",
    )
    op.create_check_constraint(
        "ck_cloud_job_queue_progress", JOB, "progress BETWEEN 0 AND 100"
    )
    op.create_check_constraint(
        "ck_cloud_job_queue_payload_object", JOB, "jsonb_typeof(payload) = 'object'"
    )
    op.create_check_constraint(
        "ck_cloud_job_queue_attempts", JOB, "attempt_count >= 0 AND max_retries >= 0"
    )
    op.create_check_constraint(
        "ck_cloud_job_queue_not_self_dependent",
        JOB,
        "depends_on_job_id IS NULL OR depends_on_job_id <> id",
    )
    op.create_check_constraint(
        "ck_cloud_job_queue_claim_state",
        JOB,
        "(status = 'processing' AND claim_token IS NOT NULL AND worker_id IS NOT NULL AND claimed_at IS NOT NULL AND heartbeat_at IS NOT NULL AND lease_expires_at IS NOT NULL) OR (status <> 'processing' AND claim_token IS NULL AND worker_id IS NULL AND claimed_at IS NULL AND heartbeat_at IS NULL AND lease_expires_at IS NULL)",
    )
    op.create_check_constraint(
        "ck_cloud_job_queue_terminal",
        JOB,
        "status NOT IN ('completed', 'failed') OR completed_at IS NOT NULL",
    )
    op.create_index(
        "ix_cloud_job_queue_claim",
        JOB,
        ["status", "scheduled_for", "priority", "created_at"],
    )
    op.create_index("ix_cloud_job_queue_lease", JOB, ["status", "lease_expires_at"])
    op.create_index("ix_cloud_job_queue_dependency", JOB, ["depends_on_job_id"])
    op.create_index(
        "uq_cloud_job_queue_active_dedupe",
        JOB,
        ["department_id", "job_type", "dedupe_key"],
        unique=True,
        postgresql_where=sa.text(
            "dedupe_key IS NOT NULL AND status IN ('pending', 'processing')"
        ),
    )

    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_id", sa.String(255), primary_key=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "heartbeat_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("jobs_claimed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("jobs_completed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("jobs_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.CheckConstraint(
            "status IN ('running', 'draining', 'stopped')",
            name="ck_worker_heartbeats_status",
        ),
    )
    op.create_index(
        "ix_worker_heartbeats_liveness", "worker_heartbeats", ["status", "heartbeat_at"]
    )


def downgrade():
    op.drop_table("worker_heartbeats")
    op.drop_index("uq_cloud_job_queue_active_dedupe", table_name=JOB)
    op.drop_index("ix_cloud_job_queue_dependency", table_name=JOB)
    op.drop_index("ix_cloud_job_queue_lease", table_name=JOB)
    op.drop_index("ix_cloud_job_queue_claim", table_name=JOB)
    for name in (
        "ck_cloud_job_queue_terminal",
        "ck_cloud_job_queue_claim_state",
        "ck_cloud_job_queue_not_self_dependent",
        "ck_cloud_job_queue_attempts",
        "ck_cloud_job_queue_payload_object",
        "ck_cloud_job_queue_progress",
        "ck_cloud_job_queue_status",
    ):
        op.drop_constraint(name, JOB, type_="check")
    op.drop_constraint("fk_cloud_job_queue_dependency", JOB, type_="foreignkey")
    for column in (
        "updated_at",
        "last_error_retryable",
        "last_error_code",
        "lease_expires_at",
        "heartbeat_at",
        "claimed_at",
        "worker_id",
        "claim_token",
        "attempt_count",
        "dedupe_key",
        "depends_on_job_id",
        "payload",
    ):
        op.drop_column(JOB, column)
