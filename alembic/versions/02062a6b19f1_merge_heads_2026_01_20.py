"""merge_heads_2026_01_20

Revision ID: 02062a6b19f1
Revises: fix_api_keys_defaults, 2026_01_19_stripe
Create Date: 2026-01-20 05:29:25.311570

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "02062a6b19f1"
down_revision: Union[str, None] = ("fix_api_keys_defaults", "2026_01_19_stripe")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
