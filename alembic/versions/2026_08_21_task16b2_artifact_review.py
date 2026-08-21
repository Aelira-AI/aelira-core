"""Task 16B2 managed artifact review and writeback audit binding.

Revision ID: 20260821_task16b2_review
Revises: 20260821_task16b1_artifacts
"""

from alembic import op
import sqlalchemy as sa

revision = "20260821_task16b2_review"
down_revision = "20260821_task16b1_artifacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in (
        "departments",
        "users",
        "cloud_oauth_credentials",
        "scans",
        "cloud_files",
    ):
        op.add_column(
            table, sa.Column("artifact_cleanup_token", sa.String(64), nullable=True)
        )
        op.add_column(
            table,
            sa.Column(
                "artifact_cleanup_claimed_at", sa.DateTime(timezone=True), nullable=True
            ),
        )
        op.create_check_constraint(
            f"ck_{table}_artifact_cleanup_fence",
            table,
            "(artifact_cleanup_token IS NULL AND artifact_cleanup_claimed_at IS NULL) OR "
            "(artifact_cleanup_token IS NOT NULL AND artifact_cleanup_claimed_at IS NOT NULL)",
        )
    op.add_column(
        "remediation_artifacts",
        sa.Column("cleanup_reason", sa.String(64), nullable=True),
    )
    op.add_column(
        "remediation_artifacts",
        sa.Column("cleanup_owner", sa.String(255), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE remediation_artifacts SET cleanup_reason = 'scheduled_cleanup', "
            "cleanup_owner = 'scheduler' WHERE cleanup_claimed_at IS NOT NULL"
        )
    )
    op.create_check_constraint(
        "ck_remediation_artifacts_cleanup_claim",
        "remediation_artifacts",
        "(cleanup_claimed_at IS NULL AND cleanup_reason IS NULL AND cleanup_owner IS NULL) OR "
        "(cleanup_claimed_at IS NOT NULL AND cleanup_reason IS NOT NULL AND cleanup_reason <> '' "
        "AND cleanup_owner IS NOT NULL AND cleanup_owner <> '')",
    )
    op.add_column(
        "scans",
        sa.Column("current_remediation_artifact_id", sa.String(36), nullable=True),
    )
    op.create_foreign_key(
        "fk_scans_current_remediation_artifact",
        "scans",
        "remediation_artifacts",
        ["current_remediation_artifact_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_scans_current_remediation_artifact_id",
        "scans",
        ["current_remediation_artifact_id"],
    )
    op.add_column(
        "content_writeback_log", sa.Column("artifact_id", sa.String(36), nullable=True)
    )
    op.add_column(
        "content_writeback_log",
        sa.Column("artifact_checksum", sa.String(64), nullable=True),
    )
    op.add_column(
        "content_writeback_log",
        sa.Column("correlation_id", sa.String(36), nullable=True),
    )
    op.add_column(
        "content_writeback_log",
        sa.Column("reconciliation_status", sa.String(32), nullable=True),
    )
    op.add_column(
        "content_writeback_log", sa.Column("provider_result", sa.JSON(), nullable=True)
    )
    op.create_foreign_key(
        "fk_content_writeback_log_artifact",
        "content_writeback_log",
        "remediation_artifacts",
        ["artifact_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_content_writeback_log_correlation_id",
        "content_writeback_log",
        ["correlation_id"],
    )
    op.create_check_constraint(
        "ck_content_writeback_log_artifact_binding",
        "content_writeback_log",
        "(artifact_id IS NULL AND artifact_checksum IS NULL) OR "
        "(artifact_id IS NOT NULL AND artifact_checksum ~ '^[0-9a-f]{64}$')",
    )
    op.create_check_constraint(
        "ck_content_writeback_log_reconciliation",
        "content_writeback_log",
        "reconciliation_status IS NULL OR reconciliation_status IN "
        "('pending', 'committed', 'reconciliation_required')",
    )
    op.create_check_constraint(
        "ck_content_writeback_log_correlation_id",
        "content_writeback_log",
        "correlation_id IS NULL OR correlation_id ~ "
        "'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_remediation_artifacts_cleanup_claim",
        "remediation_artifacts",
        type_="check",
    )
    op.drop_column("remediation_artifacts", "cleanup_owner")
    op.drop_column("remediation_artifacts", "cleanup_reason")
    op.drop_constraint(
        "ck_content_writeback_log_correlation_id",
        "content_writeback_log",
        type_="check",
    )
    op.drop_constraint(
        "ck_content_writeback_log_reconciliation",
        "content_writeback_log",
        type_="check",
    )
    op.drop_constraint(
        "ck_content_writeback_log_artifact_binding",
        "content_writeback_log",
        type_="check",
    )
    op.drop_constraint(
        "uq_content_writeback_log_correlation_id",
        "content_writeback_log",
        type_="unique",
    )
    op.drop_constraint(
        "fk_content_writeback_log_artifact",
        "content_writeback_log",
        type_="foreignkey",
    )
    op.drop_column("content_writeback_log", "provider_result")
    op.drop_column("content_writeback_log", "reconciliation_status")
    op.drop_column("content_writeback_log", "correlation_id")
    op.drop_column("content_writeback_log", "artifact_checksum")
    op.drop_column("content_writeback_log", "artifact_id")
    op.drop_index("ix_scans_current_remediation_artifact_id", table_name="scans")
    op.drop_constraint(
        "fk_scans_current_remediation_artifact", "scans", type_="foreignkey"
    )
    op.drop_column("scans", "current_remediation_artifact_id")
    for table in reversed(
        (
            "departments",
            "users",
            "cloud_oauth_credentials",
            "scans",
            "cloud_files",
        )
    ):
        op.drop_constraint(f"ck_{table}_artifact_cleanup_fence", table, type_="check")
        op.drop_column(table, "artifact_cleanup_claimed_at")
        op.drop_column(table, "artifact_cleanup_token")
