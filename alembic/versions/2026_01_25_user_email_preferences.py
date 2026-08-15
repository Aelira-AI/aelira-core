"""Add granular email notification preferences to users table

Revision ID: 2026_01_25_email_prefs
Revises: 2026_01_24_scantype
Create Date: 2026-01-25

Adds granular email notification preference fields to the users table:
- email_scan_complete: Notify when a scan finishes
- email_remediation_complete: Notify when remediation finishes
- email_critical_alerts: Alert on critical accessibility issues
- email_weekly_summary: Weekly summary reports
- email_marketing: Marketing/promotional emails (opt-in only)

Also removes the old email_notifications column which was too coarse-grained.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "2026_01_25_email_prefs"
down_revision: Union[str, None] = "2026_01_24_scantype"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def column_exists(table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table."""
    from sqlalchemy import inspect

    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [col["name"] for col in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    """Add granular email preference columns (idempotent)."""
    # Add new granular email preference columns if they don't exist
    if not column_exists("users", "email_scan_complete"):
        op.add_column(
            "users",
            sa.Column(
                "email_scan_complete",
                sa.Boolean(),
                nullable=False,
                server_default="true",
            ),
        )
    if not column_exists("users", "email_remediation_complete"):
        op.add_column(
            "users",
            sa.Column(
                "email_remediation_complete",
                sa.Boolean(),
                nullable=False,
                server_default="true",
            ),
        )
    if not column_exists("users", "email_critical_alerts"):
        op.add_column(
            "users",
            sa.Column(
                "email_critical_alerts",
                sa.Boolean(),
                nullable=False,
                server_default="true",
            ),
        )
    if not column_exists("users", "email_weekly_summary"):
        op.add_column(
            "users",
            sa.Column(
                "email_weekly_summary",
                sa.Boolean(),
                nullable=False,
                server_default="true",
            ),
        )
    if not column_exists("users", "email_marketing"):
        op.add_column(
            "users",
            sa.Column(
                "email_marketing",
                sa.Boolean(),
                nullable=False,
                server_default="false",  # Opt-in only for marketing
            ),
        )

    # Migrate existing email_notifications preference to granular fields
    # Only if the old column still exists
    if column_exists("users", "email_notifications"):
        op.execute("""
            UPDATE users
            SET email_scan_complete = email_notifications,
                email_remediation_complete = email_notifications,
                email_critical_alerts = email_notifications,
                email_weekly_summary = email_notifications
            WHERE email_notifications IS NOT NULL
        """)
        # Drop the old coarse-grained column
        op.drop_column("users", "email_notifications")


def downgrade() -> None:
    """Restore the old email_notifications column."""
    # Re-add the old column
    op.add_column(
        "users",
        sa.Column(
            "email_notifications", sa.Boolean(), nullable=False, server_default="true"
        ),
    )

    # Set based on any of the new columns being true
    op.execute("""
        UPDATE users
        SET email_notifications = (
            email_scan_complete OR
            email_remediation_complete OR
            email_critical_alerts OR
            email_weekly_summary
        )
    """)

    # Drop the new columns
    op.drop_column("users", "email_scan_complete")
    op.drop_column("users", "email_remediation_complete")
    op.drop_column("users", "email_critical_alerts")
    op.drop_column("users", "email_weekly_summary")
    op.drop_column("users", "email_marketing")
