"""No-op migration (previous merge point)

Revision ID: 2026_01_13_merge_heads
Revises: 2026_01_11_user_invitations
Create Date: 2026-01-13 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2026_01_13_merge_heads'
down_revision: Union[str, None] = '2026_01_11_user_invitations'
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
