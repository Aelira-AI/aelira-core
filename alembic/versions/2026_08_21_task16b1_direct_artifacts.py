"""Allow direct local remediation artifacts without queue jobs.

Revision ID: 20260821_task16b1_artifacts
Revises: 20260821_remediation_artifact
"""

from alembic import op

revision = "20260821_task16b1_artifacts"
down_revision = "20260821_remediation_artifact"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("remediation_artifacts", "cloud_file_id", nullable=True)
    op.alter_column("remediation_artifacts", "remediation_job_id", nullable=True)
    op.drop_constraint(
        "ck_remediation_artifacts_provider", "remediation_artifacts", type_="check"
    )
    op.create_check_constraint(
        "ck_remediation_artifacts_provider",
        "remediation_artifacts",
        "provider IN ('google', 'microsoft', 'canvas', 'blackboard', "
        "'moodle', 'brightspace', 'local')",
    )
    op.create_check_constraint(
        "ck_remediation_artifacts_provider_authority",
        "remediation_artifacts",
        "((provider = 'local' AND cloud_file_id IS NULL AND "
        "remediation_job_id IS NULL) OR "
        "(provider <> 'local' AND cloud_file_id IS NOT NULL)) AND "
        "(remediation_job_id IS NULL OR cloud_file_id IS NOT NULL)",
    )


def downgrade():
    op.drop_constraint(
        "ck_remediation_artifacts_provider_authority",
        "remediation_artifacts",
        type_="check",
    )
    op.drop_constraint(
        "ck_remediation_artifacts_provider", "remediation_artifacts", type_="check"
    )
    op.create_check_constraint(
        "ck_remediation_artifacts_provider",
        "remediation_artifacts",
        "provider IN ('google', 'microsoft', 'canvas', 'blackboard', "
        "'moodle', 'brightspace')",
    )
    op.alter_column("remediation_artifacts", "remediation_job_id", nullable=False)
    op.alter_column("remediation_artifacts", "cloud_file_id", nullable=False)
