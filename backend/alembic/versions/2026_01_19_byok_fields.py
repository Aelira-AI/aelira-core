"""Add BYOK (Bring Your Own Key) fields to departments

Revision ID: 2026_01_19_byok
Revises: 2026_01_19_image
Create Date: 2026-01-19

This migration adds BYOK-related columns to the departments table:
- byok_provider: The AI provider the department uses (gemini, openai, anthropic, ollama)
- byok_configured_at: When the department configured their own API key
- pilot_gemini_approved: Manual approval flag for high-value pilots to use founder's key

BYOK is required for pilot and department tiers to control API costs
for bootstrapped operations.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "2026_01_19_byok"
down_revision: Union[str, None] = "2026_01_19_image"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add BYOK fields to departments table."""
    # Add byok_provider column
    op.add_column(
        "departments",
        sa.Column("byok_provider", sa.String(50), nullable=True),
    )

    # Add byok_configured_at column
    op.add_column(
        "departments",
        sa.Column("byok_configured_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Add pilot_gemini_approved column
    op.add_column(
        "departments",
        sa.Column(
            "pilot_gemini_approved",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    """Remove BYOK fields from departments table."""
    op.drop_column("departments", "pilot_gemini_approved")
    op.drop_column("departments", "byok_configured_at")
    op.drop_column("departments", "byok_provider")
