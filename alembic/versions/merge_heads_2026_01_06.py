"""Merge heads 2025_11_30_country and 2026_01_06_waitlist

Revision ID: merge_heads_2026_01_06
Revises: ('2025_11_30_country', '2026_01_06_waitlist')
Create Date: 2026-01-06 13:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "merge_heads_2026_01_06"
down_revision: Union[str, None, Sequence[str]] = (
    "2025_11_30_country",
    "2026_01_06_waitlist",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
