"""Add WORD and EXCEL to scantype enum

Revision ID: 2026_01_24_scantype
Revises: 2026_01_24_contact
Create Date: 2026-01-24 12:30:00

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2026_01_24_scantype"
down_revision: Union[str, None] = "2026_01_24_contact"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add WORD and EXCEL scan types to the scantype enum."""
    op.execute("ALTER TYPE scantype ADD VALUE IF NOT EXISTS 'WORD'")
    op.execute("ALTER TYPE scantype ADD VALUE IF NOT EXISTS 'EXCEL'")


def downgrade() -> None:
    """Downgrade is not supported for enum value additions in PostgreSQL.

    PostgreSQL does not support removing enum values without recreating the entire enum,
    which would require recreating all tables/columns that use it.
    """
    pass
