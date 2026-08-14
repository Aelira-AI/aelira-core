"""Add security tables for abuse detection and document validation

Revision ID: 2026_01_14_security
Revises: 2026_01_14_individual
Create Date: 2026-01-14

This migration adds:
- signup_logs: For tracking signup attempts and detecting multi-account abuse
- security_scan_results: For tracking document security validation results
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "2026_01_14_security"
down_revision = "2026_01_14_individual"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create signup_logs table for abuse detection
    op.create_table(
        "signup_logs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email_domain", sa.String(255), nullable=False),
        sa.Column("ip_hash", sa.String(64), nullable=False),
        sa.Column("user_agent_hash", sa.String(64), nullable=True),
        sa.Column("fingerprint_hash", sa.String(64), nullable=True),
        sa.Column("success", sa.Boolean(), default=False),
        sa.Column("failure_reason", sa.String(255), nullable=True),
        sa.Column("abuse_signals", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    # Create indexes for signup_logs
    op.create_index(
        "idx_signup_logs_ip_hash",
        "signup_logs",
        ["ip_hash"],
    )
    op.create_index(
        "idx_signup_logs_email_domain",
        "signup_logs",
        ["email_domain"],
    )
    op.create_index(
        "idx_signup_logs_created_at",
        "signup_logs",
        [sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_signup_logs_fingerprint_hash",
        "signup_logs",
        ["fingerprint_hash"],
    )

    # Create security_scan_results table
    op.create_table(
        "security_scan_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "scan_id",
            sa.String(36),
            sa.ForeignKey("scans.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "department_id",
            sa.String(36),
            sa.ForeignKey("departments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("file_type", sa.String(50), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("is_safe", sa.Boolean(), nullable=False),
        sa.Column("threat_level", sa.String(20), nullable=False),
        sa.Column("findings", sa.JSON(), nullable=True),
        sa.Column("was_sanitized", sa.Boolean(), default=False),
        sa.Column("was_blocked", sa.Boolean(), default=False),
        sa.Column("blocked_reason", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    # Create indexes for security_scan_results
    op.create_index(
        "idx_security_scan_results_file_hash",
        "security_scan_results",
        ["file_hash"],
    )
    op.create_index(
        "idx_security_scan_results_department",
        "security_scan_results",
        ["department_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    # Drop indexes first
    op.drop_index("idx_security_scan_results_department", "security_scan_results")
    op.drop_index("idx_security_scan_results_file_hash", "security_scan_results")
    op.drop_index("idx_signup_logs_fingerprint_hash", "signup_logs")
    op.drop_index("idx_signup_logs_created_at", "signup_logs")
    op.drop_index("idx_signup_logs_email_domain", "signup_logs")
    op.drop_index("idx_signup_logs_ip_hash", "signup_logs")

    # Drop tables
    op.drop_table("security_scan_results")
    op.drop_table("signup_logs")
