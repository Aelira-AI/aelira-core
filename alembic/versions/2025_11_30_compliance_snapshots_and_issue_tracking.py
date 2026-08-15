"""Add compliance_snapshots and issue_tracking tables for Phase 4

Revision ID: 2025_11_30_phase4
Revises: 2025_11_12_scantype
Create Date: 2025-11-30

Phase 4 Features:
- compliance_snapshots: Daily department compliance metrics for historical trending
- issue_tracking: Persistent issue status, assignments, and resolution tracking
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "2025_11_30_phase4"
down_revision: Union[str, None] = "2025_11_12_scantype"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create enums using raw SQL with IF NOT EXISTS to prevent duplicates
    conn = op.get_bind()
    conn.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE issuestatus AS ENUM ('OPEN', 'IN_PROGRESS', 'RESOLVED', 'WONT_FIX', 'FALSE_POSITIVE');
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """))
    conn.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE issuepriority AS ENUM ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW');
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """))

    # Reference the enums using PostgreSQL dialect (properly handles existing types)
    issue_status_enum = postgresql.ENUM(
        "OPEN",
        "IN_PROGRESS",
        "RESOLVED",
        "WONT_FIX",
        "FALSE_POSITIVE",
        name="issuestatus",
        create_type=False,
    )
    issue_priority_enum = postgresql.ENUM(
        "CRITICAL", "HIGH", "MEDIUM", "LOW", name="issuepriority", create_type=False
    )

    # ==================== compliance_snapshots table ====================
    # Stores daily aggregated compliance metrics for each department
    # Used for historical trending graphs and progress tracking
    op.create_table(
        "compliance_snapshots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("department_id", sa.String(length=36), nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        # Compliance metrics
        sa.Column("avg_compliance_score", sa.Float(), nullable=False, default=0.0),
        sa.Column("min_compliance_score", sa.Float(), nullable=True),
        sa.Column("max_compliance_score", sa.Float(), nullable=True),
        # Scan counts
        sa.Column("total_scans", sa.Integer(), nullable=False, default=0),
        sa.Column("scans_today", sa.Integer(), nullable=False, default=0),
        # Issue breakdown
        sa.Column("critical_issues", sa.Integer(), nullable=False, default=0),
        sa.Column("high_issues", sa.Integer(), nullable=False, default=0),
        sa.Column("medium_issues", sa.Integer(), nullable=False, default=0),
        sa.Column("low_issues", sa.Integer(), nullable=False, default=0),
        sa.Column("total_issues", sa.Integer(), nullable=False, default=0),
        # Compliance categories
        sa.Column(
            "files_compliant", sa.Integer(), nullable=False, default=0
        ),  # Score >= 90
        sa.Column(
            "files_needs_work", sa.Integer(), nullable=False, default=0
        ),  # Score 70-89
        sa.Column(
            "files_critical", sa.Integer(), nullable=False, default=0
        ),  # Score < 70
        # Faculty metrics
        sa.Column("active_faculty", sa.Integer(), nullable=False, default=0),
        sa.Column("total_faculty", sa.Integer(), nullable=False, default=0),
        # Deadline tracking
        sa.Column("days_until_deadline", sa.Integer(), nullable=True),
        sa.Column("estimated_hours_remaining", sa.Float(), nullable=True),
        sa.Column("on_track", sa.Boolean(), nullable=True),
        # Timestamps
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        # Constraints
        sa.ForeignKeyConstraint(
            ["department_id"], ["departments.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "department_id", "snapshot_date", name="uq_snapshot_department_date"
        ),
    )

    # Create indexes for efficient querying
    op.create_index(
        "idx_snapshots_department_date",
        "compliance_snapshots",
        ["department_id", sa.text("snapshot_date DESC")],
        unique=False,
    )
    op.create_index(
        "idx_snapshots_date",
        "compliance_snapshots",
        [sa.text("snapshot_date DESC")],
        unique=False,
    )

    # ==================== issue_tracking table ====================
    # Persistent tracking of individual issues across scans
    # Enables assignment, status updates, and resolution tracking
    op.create_table(
        "issue_tracking",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("scan_id", sa.String(length=36), nullable=False),
        sa.Column("department_id", sa.String(length=36), nullable=False),
        # Issue identification (from scan results)
        sa.Column(
            "issue_hash", sa.String(length=64), nullable=False
        ),  # Unique hash of issue
        sa.Column("issue_type", sa.String(length=100), nullable=False),
        sa.Column("severity", issue_priority_enum, nullable=False),
        sa.Column(
            "wcag_criterion", sa.String(length=20), nullable=True
        ),  # e.g., "1.1.1", "2.1.1"
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "element_selector", sa.String(length=512), nullable=True
        ),  # CSS selector or location
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("slide_number", sa.Integer(), nullable=True),
        # Status tracking
        sa.Column("status", issue_status_enum, nullable=False, server_default="OPEN"),
        # Assignment
        sa.Column("assigned_to", sa.String(length=36), nullable=True),  # user_id
        sa.Column("assigned_by", sa.String(length=36), nullable=True),  # user_id
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
        # Resolution
        sa.Column("resolved_by", sa.String(length=36), nullable=True),  # user_id
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_notes", sa.Text(), nullable=True),
        sa.Column(
            "resolution_method", sa.String(length=50), nullable=True
        ),  # 'auto', 'manual', 'wont_fix'
        # Collaboration
        sa.Column("notes", sa.Text(), nullable=True),  # Team notes/discussion
        sa.Column(
            "priority_override", issue_priority_enum, nullable=True
        ),  # Manual priority adjustment
        # AI remediation tracking
        sa.Column("auto_fix_available", sa.Boolean(), nullable=True, default=False),
        sa.Column("auto_fix_applied", sa.Boolean(), nullable=True, default=False),
        sa.Column("auto_fix_result", sa.Text(), nullable=True),  # JSON with fix details
        # Timestamps
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        # Constraints
        sa.ForeignKeyConstraint(["scan_id"], ["scans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["department_id"], ["departments.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["assigned_to"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["assigned_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["resolved_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    # Create indexes for efficient querying
    op.create_index("idx_issues_scan", "issue_tracking", ["scan_id"], unique=False)
    op.create_index(
        "idx_issues_department", "issue_tracking", ["department_id"], unique=False
    )
    op.create_index("idx_issues_status", "issue_tracking", ["status"], unique=False)
    op.create_index("idx_issues_severity", "issue_tracking", ["severity"], unique=False)
    op.create_index(
        "idx_issues_assigned", "issue_tracking", ["assigned_to"], unique=False
    )
    op.create_index("idx_issues_hash", "issue_tracking", ["issue_hash"], unique=False)
    op.create_index(
        "idx_issues_department_status",
        "issue_tracking",
        ["department_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    # Drop issue_tracking table
    op.drop_index("idx_issues_department_status", table_name="issue_tracking")
    op.drop_index("idx_issues_hash", table_name="issue_tracking")
    op.drop_index("idx_issues_assigned", table_name="issue_tracking")
    op.drop_index("idx_issues_severity", table_name="issue_tracking")
    op.drop_index("idx_issues_status", table_name="issue_tracking")
    op.drop_index("idx_issues_department", table_name="issue_tracking")
    op.drop_index("idx_issues_scan", table_name="issue_tracking")
    op.drop_table("issue_tracking")

    # Drop compliance_snapshots table
    op.drop_index("idx_snapshots_date", table_name="compliance_snapshots")
    op.drop_index("idx_snapshots_department_date", table_name="compliance_snapshots")
    op.drop_table("compliance_snapshots")

    # Drop enums
    sa.Enum(name="issuepriority").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="issuestatus").drop(op.get_bind(), checkfirst=True)
