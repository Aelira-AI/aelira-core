"""Quarantine pre-v0.9.5 unowned active jobs at worker activation.

Revision ID: 20260822_v095_job_quarantine
Revises: 20260822_task21_provenance
"""

from alembic import op
import sqlalchemy as sa

revision = "20260822_v095_job_quarantine"
down_revision = "20260822_task21_provenance"
branch_labels = None
depends_on = None

REASON = "pre_v0_9_5_job_quarantined"
MESSAGE = (
    "Quarantined during v0.9.5 durable-worker activation; review and deliberately "
    "resubmit the original operation."
)


def upgrade():
    # Pre-worker rows record historical intent without proof that the initiating
    # request is still authorized or desired. Never auto-drain that intent when
    # durable execution first becomes available. Preserve payload/result and any
    # external-effect evidence for operator review, but make the job terminal and
    # release every stale claim fence.
    op.execute(
        sa.text(
            """
            UPDATE cloud_job_queue
               SET status = 'failed',
                   completed_at = now(),
                   updated_at = now(),
                   last_error_code = 'pre_v0_9_5_job_quarantined',
                   last_error_retryable = false,
                   error_message = 'Quarantined during v0.9.5 durable-worker activation; review and deliberately resubmit the original operation.',
                   claim_token = NULL,
                   worker_id = NULL,
                   claimed_at = NULL,
                   heartbeat_at = NULL,
                   lease_expires_at = NULL
             WHERE status IN ('pending', 'processing')
            """
        )
    )


def downgrade():
    # Moving the revision marker backwards must never revive historical intent.
    pass
