"""Add optimistic revision to department LMS AI policy.

Revision ID: 20260821_lms_policy_rev
Revises: 20260820_job_exec_context
"""

from alembic import op
import sqlalchemy as sa

revision = "20260821_lms_policy_rev"
down_revision = "20260820_job_exec_context"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "departments",
        sa.Column(
            "lms_ai_policy_revision",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade():
    op.drop_column("departments", "lms_ai_policy_revision")
