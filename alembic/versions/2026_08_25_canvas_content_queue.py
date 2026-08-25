"""Add bounded Canvas stored-content remediation evidence.

Revision ID: 20260825_canvas_queue
Revises: 20260824_task8_review
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260825_canvas_queue"
down_revision = "20260824_task8_review"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(JSONB(), "postgresql")
_TABLE = "canvas_content_remediation_evidence"


def _lower_hex_64(column: str) -> str:
    stripped = column
    for character in "0123456789abcdef":
        stripped = f"replace({stripped}, '{character}', '')"
    return f"length({column}) = 64 AND {column} = lower({column}) AND {stripped} = ''"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "department_id",
            sa.String(36),
            sa.ForeignKey("departments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "cloud_file_id",
            sa.String(36),
            sa.ForeignKey("cloud_files.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("candidate_sha256", sa.String(64), nullable=False),
        sa.Column("source_scan_id", sa.String(36)),
        sa.Column("producer_job_id", sa.String(36)),
        sa.Column("quarantine_reason", sa.String(64), nullable=False),
        sa.Column(
            "diagnostics",
            _JSON,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("stored_bytes", sa.Integer(), nullable=False),
        sa.Column(
            "lifecycle_state",
            sa.String(16),
            nullable=False,
            server_default="current",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"{_lower_hex_64('source_sha256')} AND "
            f"{_lower_hex_64('candidate_sha256')}",
            name="ck_canvas_content_evidence_hashes",
        ),
        sa.CheckConstraint(
            "stored_bytes BETWEEN 1 AND 4096",
            name="ck_canvas_content_evidence_size",
        ),
        sa.CheckConstraint(
            "length(quarantine_reason) BETWEEN 1 AND 64",
            name="ck_canvas_content_evidence_reason",
        ),
        sa.CheckConstraint(
            "lifecycle_state IN ('current', 'expired')",
            name="ck_canvas_content_evidence_lifecycle",
        ),
    )
    op.create_index(
        "ix_canvas_content_evidence_file_created",
        _TABLE,
        ["cloud_file_id", "created_at"],
    )
    op.create_index(
        "ix_canvas_content_evidence_department_expires",
        _TABLE,
        ["department_id", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_canvas_content_evidence_department_expires", table_name=_TABLE)
    op.drop_index("ix_canvas_content_evidence_file_created", table_name=_TABLE)
    op.drop_table(_TABLE)
