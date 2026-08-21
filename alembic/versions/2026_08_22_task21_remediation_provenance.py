"""Persist remediation provenance on durable cloud-file state.

Revision ID: 20260822_task21_provenance
Revises: 20260822_task18_identity
"""

from alembic import op
import sqlalchemy as sa

revision = "20260822_task21_provenance"
down_revision = "20260822_task18_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cloud_files",
        sa.Column("remediation_origin", sa.String(16), nullable=True),
    )
    op.create_check_constraint(
        "ck_cloud_files_remediation_origin",
        "cloud_files",
        "remediation_origin IS NULL OR remediation_origin IN ('automatic', 'manual')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_cloud_files_remediation_origin",
        "cloud_files",
        type_="check",
    )
    op.drop_column("cloud_files", "remediation_origin")
