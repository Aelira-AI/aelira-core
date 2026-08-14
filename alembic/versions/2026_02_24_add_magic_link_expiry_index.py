"""Add index on magic_links.expires_at for efficient cleanup queries

Revision ID: 2026_02_24_magic_link_idx
Revises: 2026_02_06_expiry_indexes
Create Date: 2026-02-24

Adds index on MagicLink.expires_at to improve performance of
periodic cleanup that deletes expired magic links.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "2026_02_24_magic_link_idx"
down_revision: Union[str, None] = "2026_02_06_expiry_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def index_exists(index_name: str) -> bool:
    """Check if an index exists (idempotent helper)."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table_name in inspector.get_table_names():
        indexes = inspector.get_indexes(table_name)
        if any(idx["name"] == index_name for idx in indexes):
            return True
    return False


def upgrade() -> None:
    """Add index on magic_links.expires_at."""
    if not index_exists("idx_magic_links_expires_at"):
        op.create_index(
            "idx_magic_links_expires_at",
            "magic_links",
            ["expires_at"],
        )


def downgrade() -> None:
    """Remove magic_links expires_at index."""
    if index_exists("idx_magic_links_expires_at"):
        op.drop_index("idx_magic_links_expires_at", table_name="magic_links")
