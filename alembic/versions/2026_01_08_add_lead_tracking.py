"""Add lead tracking columns to waitlist

Revision ID: 2026_01_08_lead_tracking
Revises: merge_heads_2026_01_06
Create Date: 2026-01-08 10:00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "2026_01_08_lead_tracking"
down_revision: Union[str, None] = "merge_heads_2026_01_06"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add lead tracking columns to waitlist_signups table.

    Columns added:
    - institution_domain: Domain extracted from email (e.g., 'stanford.edu')
    - status: Lead status tracking ('new', 'contacted', 'demo_scheduled', 'pilot', 'converted', 'churned')
    - notes: Free-form notes for sales/support team
    - contacted_at: Timestamp of first outreach
    """
    op.add_column(
        "waitlist_signups",
        sa.Column(
            "institution_domain",
            sa.String(255),
            nullable=True,
            comment="Domain extracted from email address",
        ),
    )
    op.add_column(
        "waitlist_signups",
        sa.Column(
            "status",
            sa.String(50),
            server_default="new",
            nullable=False,
            comment="Lead status: new, contacted, demo_scheduled, pilot, converted, churned",
        ),
    )
    op.add_column(
        "waitlist_signups",
        sa.Column(
            "notes",
            sa.Text,
            nullable=True,
            comment="Internal notes for sales/support team",
        ),
    )
    op.add_column(
        "waitlist_signups",
        sa.Column(
            "contacted_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Timestamp of first outreach",
        ),
    )

    # Create index on status for filtering leads by status
    op.create_index(
        "ix_waitlist_signups_status", "waitlist_signups", ["status"], unique=False
    )

    # Create index on institution_domain for grouping leads by institution
    op.create_index(
        "ix_waitlist_signups_institution_domain",
        "waitlist_signups",
        ["institution_domain"],
        unique=False,
    )


def downgrade() -> None:
    """Remove lead tracking columns from waitlist_signups table."""
    # Drop indexes first
    op.drop_index(
        "ix_waitlist_signups_institution_domain", table_name="waitlist_signups"
    )
    op.drop_index("ix_waitlist_signups_status", table_name="waitlist_signups")

    # Drop columns
    op.drop_column("waitlist_signups", "contacted_at")
    op.drop_column("waitlist_signups", "notes")
    op.drop_column("waitlist_signups", "status")
    op.drop_column("waitlist_signups", "institution_domain")
