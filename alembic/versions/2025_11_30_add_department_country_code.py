"""Add country_code to departments for region-specific deadline tracking

Revision ID: 2025_11_30_country
Revises: 2025_11_30_phase4
Create Date: 2025-11-30

This migration adds:
1. country_code column to departments (ISO 3166-1 alpha-2)
2. regulatory_framework column for specific compliance standards

Region-specific deadlines:
- US: April 24, 2026 (DOJ Title II ADA WCAG 2.2 AA)
- EU: June 28, 2025 (European Accessibility Act)
- UK: September 23, 2024 (PSBAR - already passed, but tracking compliance)
- CA: January 1, 2025 (AODA)
- AU: No specific deadline (DDA general compliance)
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "2025_11_30_country"
down_revision: Union[str, None] = "2025_11_30_phase4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add country_code and regulatory_framework to departments."""
    # Add country_code column (ISO 3166-1 alpha-2)
    op.add_column(
        "departments",
        sa.Column("country_code", sa.String(2), nullable=True, default="US"),
    )

    # Add regulatory_framework column (which standard applies)
    op.add_column(
        "departments",
        sa.Column(
            "regulatory_framework",
            sa.String(50),
            nullable=True,
            default="US_ADA_TITLE_II",
        ),
    )

    # Add custom_deadline column (for organizations with specific deadlines)
    op.add_column(
        "departments",
        sa.Column("custom_deadline", sa.DateTime(timezone=True), nullable=True),
    )

    # Add timezone column for proper date handling
    op.add_column(
        "departments",
        sa.Column("timezone", sa.String(50), nullable=True, default="America/New_York"),
    )


def downgrade() -> None:
    """Remove country and deadline columns from departments."""
    op.drop_column("departments", "timezone")
    op.drop_column("departments", "custom_deadline")
    op.drop_column("departments", "regulatory_framework")
    op.drop_column("departments", "country_code")
