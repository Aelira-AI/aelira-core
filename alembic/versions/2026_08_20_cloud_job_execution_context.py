"""Add queued-job execution context and durable remediation outcomes.

Revision ID: 20260820_job_exec_context
Revises: 20260820_lms_ai_policy
"""

from alembic import op
import sqlalchemy as sa

revision = "20260820_job_exec_context"
down_revision = "20260820_lms_ai_policy"
branch_labels = None
depends_on = None

JOB_TABLE = "cloud_job_queue"
JOB_COLUMN = "execution_context"
SCAN_TABLE = "scans"
OUTCOME_COLUMN = "remediation_outcome"


def upgrade():
    op.add_column(
        JOB_TABLE,
        sa.Column(
            JOB_COLUMN,
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        SCAN_TABLE,
        sa.Column(OUTCOME_COLUMN, sa.String(length=32), nullable=True),
    )


def downgrade():
    op.drop_column(SCAN_TABLE, OUTCOME_COLUMN)
    op.drop_column(JOB_TABLE, JOB_COLUMN)
