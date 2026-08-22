"""Add managed remediation artifact authority.

Revision ID: 20260821_remediation_artifact
Revises: 20260821_lms_policy_rev
"""

from alembic import op
import sqlalchemy as sa

revision = "20260821_remediation_artifact"
down_revision = "20260821_lms_policy_rev"
branch_labels = None
depends_on = None

CURRENT_ARTIFACT_FK = "fk_cloud_files_current_artifact"
CURRENT_ARTIFACT_INDEX = "ix_cloud_files_current_remediation_artifact_id"


def upgrade():
    op.create_table(
        "remediation_artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("department_id", sa.String(length=36), nullable=False),
        sa.Column("scan_id", sa.String(length=36), nullable=False),
        sa.Column("cloud_file_id", sa.String(length=36), nullable=False),
        sa.Column("remediation_job_id", sa.String(length=36), nullable=False),
        sa.Column("created_by_id", sa.String(length=36), nullable=True),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("scan_type", sa.String(length=32), nullable=False),
        sa.Column("publication_token", sa.String(length=64), nullable=True),
        sa.Column(
            "publication_heartbeat_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "storage_backend",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'local'"),
        ),
        sa.Column("storage_key", sa.String(length=1024), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("mime_type", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "lifecycle_status",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'staging'"),
        ),
        sa.Column(
            "review_status",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("approval_checksum", sa.String(length=64), nullable=True),
        sa.Column("approved_by_id", sa.String(length=36), nullable=True),
        sa.Column("approved_by_ref", sa.String(length=255), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_by_id", sa.String(length=36), nullable=True),
        sa.Column("rejected_by_ref", sa.String(length=255), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("written_back_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleanup_claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_result", sa.JSON(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "provider IN ('google', 'microsoft', 'canvas', 'blackboard', "
            "'moodle', 'brightspace')",
            name="ck_remediation_artifacts_provider",
        ),
        sa.CheckConstraint(
            "scan_type IN ('PDF', 'POWERPOINT', 'WORD', 'EXCEL', 'LATEX', "
            "'IMAGE', 'WEBSITE', 'CANVAS_CONTENT')",
            name="ck_remediation_artifacts_scan_type",
        ),
        sa.CheckConstraint(
            "(lifecycle_status = 'staging' AND publication_token IS NOT NULL AND "
            "publication_heartbeat_at IS NOT NULL) OR "
            "(lifecycle_status <> 'staging' AND publication_token IS NULL AND "
            "publication_heartbeat_at IS NULL)",
            name="ck_remediation_artifacts_publication_lease",
        ),
        sa.CheckConstraint(
            "storage_backend = 'local'",
            name="ck_remediation_artifacts_storage_backend",
        ),
        sa.CheckConstraint(
            "storage_key <> '' AND storage_key NOT LIKE '/%' AND "
            "storage_key NOT LIKE '%..%' AND storage_key NOT LIKE '%\\\\%'",
            name="ck_remediation_artifacts_storage_key",
        ),
        sa.CheckConstraint("size_bytes >= 0", name="ck_remediation_artifacts_size"),
        sa.CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$'", name="ck_remediation_artifacts_sha256"
        ),
        sa.CheckConstraint(
            "lifecycle_status IN "
            "('available', 'staging', 'expired', 'deleted', 'superseded')",
            name="ck_remediation_artifacts_lifecycle",
        ),
        sa.CheckConstraint(
            "review_status IN ('pending', 'approved', 'rejected')",
            name="ck_remediation_artifacts_review",
        ),
        sa.CheckConstraint(
            "(review_status = 'pending' AND approval_checksum IS NULL AND "
            "approved_by_id IS NULL AND approved_by_ref IS NULL AND "
            "approved_at IS NULL AND rejected_by_id IS NULL AND "
            "rejected_by_ref IS NULL AND rejected_at IS NULL) OR "
            "(review_status = 'approved' AND approval_checksum IS NOT NULL AND "
            "approved_by_ref IS NOT NULL AND approved_by_ref <> '' AND "
            "approved_at IS NOT NULL AND rejected_by_id IS NULL AND "
            "rejected_by_ref IS NULL AND rejected_at IS NULL) OR "
            "(review_status = 'rejected' AND approval_checksum IS NULL AND "
            "approved_by_id IS NULL AND approved_by_ref IS NULL AND "
            "approved_at IS NULL AND rejected_by_ref IS NOT NULL AND "
            "rejected_by_ref <> '' AND rejected_at IS NOT NULL)",
            name="ck_remediation_artifacts_review_metadata",
        ),
        sa.CheckConstraint(
            "written_back_at IS NULL OR review_status = 'approved'",
            name="ck_remediation_artifacts_written",
        ),
        sa.CheckConstraint(
            "deleted_at IS NULL OR lifecycle_status = 'deleted'",
            name="ck_remediation_artifacts_deleted",
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name="ck_remediation_artifacts_expiry"
        ),
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["departments.id"],
            name="fk_remediation_artifacts_department",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["scan_id"],
            ["scans.id"],
            name="fk_remediation_artifacts_scan",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["cloud_file_id"],
            ["cloud_files.id"],
            name="fk_remediation_artifacts_cloud_file",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["remediation_job_id"],
            ["cloud_job_queue.id"],
            name="fk_remediation_artifacts_remediation_job",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["approved_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["rejected_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("remediation_job_id"),
        sa.UniqueConstraint("publication_token"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index(
        "ix_remediation_artifacts_department_lifecycle_expires",
        "remediation_artifacts",
        ["department_id", "lifecycle_status", "expires_at"],
    )
    op.create_index(
        "ix_remediation_artifacts_scan_created",
        "remediation_artifacts",
        ["scan_id", "created_at"],
    )
    op.create_index(
        "ix_remediation_artifacts_cloud_file_review",
        "remediation_artifacts",
        ["cloud_file_id", "review_status"],
    )
    op.create_index(
        "ix_remediation_artifacts_cleanup_claimed_at",
        "remediation_artifacts",
        ["cleanup_claimed_at"],
    )
    op.create_index(
        "ix_remediation_artifacts_publication_heartbeat_at",
        "remediation_artifacts",
        ["publication_heartbeat_at"],
    )
    op.create_index(
        "ix_remediation_artifacts_published_at",
        "remediation_artifacts",
        ["published_at"],
    )
    op.create_index(
        "ix_remediation_artifacts_staging_heartbeat",
        "remediation_artifacts",
        ["lifecycle_status", "publication_heartbeat_at"],
    )
    op.add_column(
        "cloud_files",
        sa.Column(
            "current_remediation_artifact_id", sa.String(length=36), nullable=True
        ),
    )
    op.create_index(
        CURRENT_ARTIFACT_INDEX,
        "cloud_files",
        ["current_remediation_artifact_id"],
    )
    op.create_foreign_key(
        CURRENT_ARTIFACT_FK,
        "cloud_files",
        "remediation_artifacts",
        ["current_remediation_artifact_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade():
    op.drop_index(CURRENT_ARTIFACT_INDEX, table_name="cloud_files")
    op.drop_index(
        "ix_remediation_artifacts_staging_heartbeat",
        table_name="remediation_artifacts",
    )
    op.drop_index(
        "ix_remediation_artifacts_published_at", table_name="remediation_artifacts"
    )
    op.drop_index(
        "ix_remediation_artifacts_publication_heartbeat_at",
        table_name="remediation_artifacts",
    )
    op.drop_index(
        "ix_remediation_artifacts_cleanup_claimed_at",
        table_name="remediation_artifacts",
    )
    op.drop_index(
        "ix_remediation_artifacts_cloud_file_review",
        table_name="remediation_artifacts",
    )
    op.drop_index(
        "ix_remediation_artifacts_scan_created", table_name="remediation_artifacts"
    )
    op.drop_index(
        "ix_remediation_artifacts_department_lifecycle_expires",
        table_name="remediation_artifacts",
    )
    op.drop_constraint(CURRENT_ARTIFACT_FK, "cloud_files", type_="foreignkey")
    op.drop_column("cloud_files", "current_remediation_artifact_id")
    op.drop_table("remediation_artifacts")
