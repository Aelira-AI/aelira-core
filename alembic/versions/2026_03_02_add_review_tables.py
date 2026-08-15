"""Add scan_fixes, matterhorn_results, and review_audit_log tables

Revision ID: 2026_03_02_review_tables
Revises: 2026_02_24_magic_link_idx
Create Date: 2026-03-02

Adds tables for remediation fix tracking with confidence scoring,
Matterhorn Protocol checkpoint results, and review audit trail.
These tables support the human-in-the-loop review workflow for
AI-generated accessibility fixes.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

# revision identifiers, used by Alembic.
revision: str = "2026_03_02_review_tables"
down_revision: Union[str, None] = "2026_02_24_magic_link_idx"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def table_exists(table_name: str) -> bool:
    """Check if a table exists (idempotent helper)."""
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    return table_name in inspector.get_table_names()


def index_exists(index_name: str) -> bool:
    """Check if an index exists (idempotent helper)."""
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    for table_name in inspector.get_table_names():
        indexes = inspector.get_indexes(table_name)
        if any(idx["name"] == index_name for idx in indexes):
            return True
    return False


def upgrade() -> None:
    # ── scan_fixes ──────────────────────────────────────────────────────
    if not table_exists("scan_fixes"):
        op.create_table(
            "scan_fixes",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "scan_id",
                sa.String(36),
                sa.ForeignKey("scans.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("issue_id", sa.String(), nullable=False),
            sa.Column("category", sa.String(50), nullable=False),
            sa.Column("severity", sa.String(20), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("location", sa.String(255), nullable=True),
            sa.Column("original_content", sa.Text(), nullable=True),
            sa.Column("fixed_content", sa.Text(), nullable=False),
            sa.Column("fix_method", sa.String(20), nullable=False),
            sa.Column("model_used", sa.String(50), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
            sa.Column(
                "needs_review",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column(
                "review_status",
                sa.String(20),
                nullable=False,
                server_default="pending",
            ),
            sa.Column(
                "reviewed_by",
                sa.String(36),
                sa.ForeignKey("users.id"),
                nullable=True,
            ),
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("review_notes", sa.Text(), nullable=True),
            sa.Column("wcag_criteria", sa.String(20), nullable=True),
            sa.Column("page_number", sa.Integer(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )

    if not index_exists("idx_scan_fixes_scan_id"):
        op.create_index("idx_scan_fixes_scan_id", "scan_fixes", ["scan_id"])

    if not index_exists("idx_scan_fixes_review"):
        op.create_index(
            "idx_scan_fixes_review", "scan_fixes", ["needs_review", "review_status"]
        )

    if not index_exists("idx_scan_fixes_confidence"):
        op.create_index("idx_scan_fixes_confidence", "scan_fixes", ["confidence"])

    # ── matterhorn_results ──────────────────────────────────────────────
    if not table_exists("matterhorn_results"):
        op.create_table(
            "matterhorn_results",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "scan_id",
                sa.String(36),
                sa.ForeignKey("scans.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("checkpoint_id", sa.String(20), nullable=False),
            sa.Column("checkpoint_name", sa.String(255), nullable=False),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("severity", sa.String(20), nullable=True),
            sa.Column("details", sa.Text(), nullable=True),
            sa.Column("page_number", sa.Integer(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
            ),
        )

    if not index_exists("idx_matterhorn_scan_id"):
        op.create_index("idx_matterhorn_scan_id", "matterhorn_results", ["scan_id"])

    # ── review_audit_log ────────────────────────────────────────────────
    if not table_exists("review_audit_log"):
        op.create_table(
            "review_audit_log",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "scan_id",
                sa.String(36),
                sa.ForeignKey("scans.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "fix_id",
                sa.String(36),
                sa.ForeignKey("scan_fixes.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "user_id",
                sa.String(36),
                sa.ForeignKey("users.id"),
                nullable=True,
            ),
            sa.Column("action", sa.String(50), nullable=False),
            sa.Column("details", sa.JSON(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
            ),
        )

    if not index_exists("idx_review_audit_scan_id"):
        op.create_index("idx_review_audit_scan_id", "review_audit_log", ["scan_id"])


def downgrade() -> None:
    # Drop indexes then tables in reverse order
    if index_exists("idx_review_audit_scan_id"):
        op.drop_index("idx_review_audit_scan_id", table_name="review_audit_log")
    if table_exists("review_audit_log"):
        op.drop_table("review_audit_log")

    if index_exists("idx_matterhorn_scan_id"):
        op.drop_index("idx_matterhorn_scan_id", table_name="matterhorn_results")
    if table_exists("matterhorn_results"):
        op.drop_table("matterhorn_results")

    if index_exists("idx_scan_fixes_confidence"):
        op.drop_index("idx_scan_fixes_confidence", table_name="scan_fixes")
    if index_exists("idx_scan_fixes_review"):
        op.drop_index("idx_scan_fixes_review", table_name="scan_fixes")
    if index_exists("idx_scan_fixes_scan_id"):
        op.drop_index("idx_scan_fixes_scan_id", table_name="scan_fixes")
    if table_exists("scan_fixes"):
        op.drop_table("scan_fixes")
