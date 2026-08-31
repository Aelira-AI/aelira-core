"""Store color-vision-deficiency analysis evidence on scan results.

Revision ID: 20260830_cvd_metrics
Revises: 20260830_weekly_summary
"""

from alembic import op
import sqlalchemy as sa

revision = "20260830_cvd_metrics"
down_revision = "20260830_weekly_summary"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scan_results",
        sa.Column("cvd_analysis", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("scan_results", "cvd_analysis")
