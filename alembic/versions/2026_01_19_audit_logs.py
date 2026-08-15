"""add audit_logs table for security logging

Revision ID: 2026_01_19_audit
Revises: 2026_01_18_auth
Create Date: 2026-01-19

This migration creates the audit_logs table for tracking security-sensitive
actions such as logins, logouts, API key creation, session management, etc.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "2026_01_19_audit"
down_revision: Union[str, None] = "2026_01_18_auth"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create audit_logs table
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "department_id",
            sa.String(36),
            sa.ForeignKey("departments.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("resource_type", sa.String(50), nullable=True),
        sa.Column("resource_id", sa.String(36), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(512), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    # Create indexes for efficient queries
    op.create_index(
        "idx_audit_logs_user_created",
        "audit_logs",
        ["user_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_audit_logs_department_created",
        "audit_logs",
        ["department_id", sa.text("created_at DESC")],
    )
    op.create_index("idx_audit_logs_action", "audit_logs", ["action"])
    op.create_index("idx_audit_logs_created_at", "audit_logs", ["created_at"])


def downgrade() -> None:
    # Drop indexes
    op.drop_index("idx_audit_logs_created_at", table_name="audit_logs")
    op.drop_index("idx_audit_logs_action", table_name="audit_logs")
    op.drop_index("idx_audit_logs_department_created", table_name="audit_logs")
    op.drop_index("idx_audit_logs_user_created", table_name="audit_logs")

    # Drop table
    op.drop_table("audit_logs")
