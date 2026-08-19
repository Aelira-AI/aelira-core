"""Backfill and constrain department weekly alert schedules.

Revision ID: 20260819_alert_schedule
Revises: 20260819_session_refresh
"""

from alembic import op
import sqlalchemy as sa

revision = "20260819_alert_schedule"
down_revision = "20260819_session_refresh"
branch_labels = None
depends_on = None

TABLE = "email_alert_settings"
DAY_CHECK = "ck_email_alert_settings_weekly_summary_day_range"
HOUR_CHECK = "ck_email_alert_settings_weekly_summary_hour_range"


def upgrade():
    # Repair both NULLs and out-of-range legacy values before enforcing constraints.
    op.execute(
        sa.text(
            "UPDATE email_alert_settings "
            "SET weekly_summary_day = 0 "
            "WHERE weekly_summary_day IS NULL "
            "OR weekly_summary_day < 0 OR weekly_summary_day > 6"
        )
    )
    op.execute(
        sa.text(
            "UPDATE email_alert_settings "
            "SET weekly_summary_hour = 9 "
            "WHERE weekly_summary_hour IS NULL "
            "OR weekly_summary_hour < 0 OR weekly_summary_hour > 23"
        )
    )
    op.alter_column(
        TABLE,
        "weekly_summary_day",
        existing_type=sa.Integer(),
        nullable=False,
        server_default=sa.text("0"),
    )
    op.alter_column(
        TABLE,
        "weekly_summary_hour",
        existing_type=sa.Integer(),
        nullable=False,
        server_default=sa.text("9"),
    )
    op.create_check_constraint(DAY_CHECK, TABLE, "weekly_summary_day BETWEEN 0 AND 6")
    op.create_check_constraint(
        HOUR_CHECK, TABLE, "weekly_summary_hour BETWEEN 0 AND 23"
    )


def downgrade():
    # Restore the prior schema without undoing repaired schedule data.
    op.drop_constraint(HOUR_CHECK, TABLE, type_="check")
    op.drop_constraint(DAY_CHECK, TABLE, type_="check")
    op.alter_column(
        TABLE,
        "weekly_summary_hour",
        existing_type=sa.Integer(),
        nullable=True,
        server_default=None,
    )
    op.alter_column(
        TABLE,
        "weekly_summary_day",
        existing_type=sa.Integer(),
        nullable=True,
        server_default=None,
    )
