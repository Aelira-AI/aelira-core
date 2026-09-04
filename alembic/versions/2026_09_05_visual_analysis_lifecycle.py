"""Add durable image and chart analysis lifecycle.

Revision ID: 20260905_visual_analysis
Revises: 20260905_review_deferrals
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260905_visual_analysis"
down_revision = "20260905_review_deferrals"
branch_labels = None
depends_on = None

_ANALYSIS_TABLE = "visual_analyses"
_ATTEMPT_TABLE = "visual_analysis_attempts"
_JSON = sa.JSON().with_variant(JSONB(), "postgresql")


def _lower_hex_64(column: str) -> str:
    stripped = column
    for character in "0123456789abcdef":
        stripped = f"replace({stripped}, '{character}', '')"
    return f"length({column}) = 64 AND {column} = lower({column}) AND {stripped} = ''"


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if _ANALYSIS_TABLE not in tables:
        op.create_table(
            _ANALYSIS_TABLE,
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("department_id", sa.String(length=36), nullable=False),
            sa.Column("scan_id", sa.String(length=36), nullable=False),
            sa.Column("review_fix_id", sa.String(length=36), nullable=True),
            sa.Column("source_kind", sa.String(length=16), nullable=False),
            sa.Column("parent_artifact_sha256", sa.String(length=64), nullable=False),
            sa.Column("source_sha256", sa.String(length=64), nullable=True),
            sa.Column("source_locator", _JSON, nullable=False),
            sa.Column("purpose", sa.String(length=32), nullable=False),
            sa.Column("request_digest", sa.String(length=64), nullable=False),
            sa.Column(
                "status",
                sa.String(length=32),
                nullable=False,
                server_default="queued",
            ),
            sa.Column("proposal", _JSON, nullable=True),
            sa.Column("proposal_sha256", sa.String(length=64), nullable=True),
            sa.Column("failure_category", sa.String(length=64), nullable=True),
            sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
            sa.Column(
                "attempt_count", sa.Integer(), nullable=False, server_default="0"
            ),
            sa.Column("claim_token", sa.String(length=36), nullable=True),
            sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=True,
                server_default=sa.func.now(),
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.CheckConstraint(
                "source_kind IN ('image', 'chart')",
                name="ck_visual_analyses_source_kind",
            ),
            sa.CheckConstraint(
                _lower_hex_64("parent_artifact_sha256"),
                name="ck_visual_analyses_parent_digest",
            ),
            sa.CheckConstraint(
                "source_sha256 IS NULL OR " + f"({_lower_hex_64('source_sha256')})",
                name="ck_visual_analyses_source_digest",
            ),
            sa.CheckConstraint(
                _lower_hex_64("request_digest"),
                name="ck_visual_analyses_request_digest",
            ),
            sa.CheckConstraint(
                "proposal_sha256 IS NULL OR " + f"({_lower_hex_64('proposal_sha256')})",
                name="ck_visual_analyses_proposal_digest",
            ),
            sa.CheckConstraint(
                "status IN ('queued', 'running', 'succeeded', "
                "'retryable_failure', 'terminal_failure', 'review_required')",
                name="ck_visual_analyses_status",
            ),
            sa.CheckConstraint(
                "purpose IN ('alt_text', 'chart_description', 'image_type', "
                "'alt_text_validation', 'audio_description')",
                name="ck_visual_analyses_purpose",
            ),
            sa.CheckConstraint(
                "max_attempts >= 1 AND max_attempts <= 20 AND "
                "attempt_count >= 0 AND attempt_count <= max_attempts",
                name="ck_visual_analyses_attempt_counts",
            ),
            sa.CheckConstraint(
                "(proposal IS NULL AND proposal_sha256 IS NULL) OR "
                "(proposal IS NOT NULL AND proposal_sha256 IS NOT NULL)",
                name="ck_visual_analyses_proposal_pair",
            ),
            sa.CheckConstraint(
                "(status = 'running' AND claim_token IS NOT NULL AND "
                "claimed_at IS NOT NULL AND heartbeat_at IS NOT NULL AND "
                "lease_expires_at IS NOT NULL) OR "
                "(status <> 'running' AND claim_token IS NULL AND "
                "claimed_at IS NULL AND heartbeat_at IS NULL AND "
                "lease_expires_at IS NULL)",
                name="ck_visual_analyses_claim_fence",
            ),
            sa.ForeignKeyConstraint(
                ["department_id"], ["departments.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(["scan_id"], ["scans.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["review_fix_id"], ["scan_fixes.id"], ondelete="SET NULL"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "department_id",
                "request_digest",
                name="uq_visual_analyses_department_request",
            ),
        )
        op.create_index(
            "idx_visual_analyses_scan_status",
            _ANALYSIS_TABLE,
            ["scan_id", "status"],
            unique=False,
        )
        op.create_index(
            "idx_visual_analyses_recovery",
            _ANALYSIS_TABLE,
            ["status", "lease_expires_at"],
            unique=False,
        )

    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if _ATTEMPT_TABLE not in tables:
        op.create_table(
            _ATTEMPT_TABLE,
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("analysis_id", sa.String(length=36), nullable=False),
            sa.Column("attempt_number", sa.Integer(), nullable=False),
            sa.Column("purpose", sa.String(length=32), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("provider", sa.String(length=64), nullable=True),
            sa.Column("model", sa.String(length=200), nullable=True),
            sa.Column("failure_category", sa.String(length=64), nullable=True),
            sa.Column("proposal", _JSON, nullable=True),
            sa.Column("proposal_sha256", sa.String(length=64), nullable=True),
            sa.CheckConstraint(
                "attempt_number >= 1 AND attempt_number <= 20",
                name="ck_visual_analysis_attempts_number",
            ),
            sa.CheckConstraint(
                "status IN ('running', 'succeeded', 'retryable_failure', "
                "'terminal_failure')",
                name="ck_visual_analysis_attempts_status",
            ),
            sa.CheckConstraint(
                "purpose IN ('alt_text', 'chart_description', 'image_type', "
                "'alt_text_validation', 'audio_description')",
                name="ck_visual_analysis_attempts_purpose",
            ),
            sa.CheckConstraint(
                "(status = 'running' AND finished_at IS NULL) OR "
                "(status <> 'running' AND finished_at IS NOT NULL)",
                name="ck_visual_analysis_attempts_finish",
            ),
            sa.CheckConstraint(
                "(proposal IS NULL AND proposal_sha256 IS NULL) OR "
                "(proposal IS NOT NULL AND proposal_sha256 IS NOT NULL)",
                name="ck_visual_analysis_attempts_proposal_pair",
            ),
            sa.CheckConstraint(
                "proposal_sha256 IS NULL OR " + f"({_lower_hex_64('proposal_sha256')})",
                name="ck_visual_analysis_attempts_proposal_digest",
            ),
            sa.CheckConstraint(
                "failure_category IS NULL OR status IN "
                "('retryable_failure', 'terminal_failure')",
                name="ck_visual_analysis_attempts_failure_state",
            ),
            sa.ForeignKeyConstraint(
                ["analysis_id"], [_ANALYSIS_TABLE + ".id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "analysis_id",
                "attempt_number",
                name="uq_visual_analysis_attempt_number",
            ),
        )
        op.create_index(
            "idx_visual_analysis_attempts_analysis",
            _ATTEMPT_TABLE,
            ["analysis_id"],
            unique=False,
        )


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if _ATTEMPT_TABLE in tables:
        op.drop_table(_ATTEMPT_TABLE)
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if _ANALYSIS_TABLE in tables:
        op.drop_table(_ANALYSIS_TABLE)
