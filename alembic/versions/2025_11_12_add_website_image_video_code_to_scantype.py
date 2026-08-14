"""add WEBSITE, IMAGE, VIDEO, CODE to scantype enum

Revision ID: 2025_11_12_scantype
Revises: 2025_11_09_pa11y_multi_engine_results
Create Date: 2025-11-12 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2025_11_12_scantype'
down_revision: Union[str, None] = '1b21aeb48f28'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add new scan types to the scantype enum."""
    # PostgreSQL requires ALTER TYPE to add new enum values
    # This must be done outside a transaction block in production
    # For safety, we'll add them one at a time

    op.execute("ALTER TYPE scantype ADD VALUE IF NOT EXISTS 'IMAGE'")
    op.execute("ALTER TYPE scantype ADD VALUE IF NOT EXISTS 'VIDEO'")
    op.execute("ALTER TYPE scantype ADD VALUE IF NOT EXISTS 'WEBSITE'")
    op.execute("ALTER TYPE scantype ADD VALUE IF NOT EXISTS 'CODE'")


def downgrade() -> None:
    """Downgrade is not supported for enum value additions in PostgreSQL.

    PostgreSQL does not support removing enum values without recreating the entire enum,
    which would require recreating all tables/columns that use it.
    """
    # Cannot remove enum values in PostgreSQL without recreating the entire type
    # which would require:
    # 1. Create new enum with old values
    # 2. Alter all columns to use new enum
    # 3. Drop old enum
    # This is dangerous in production, so we don't support downgrade
    pass
