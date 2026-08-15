"""Add indexes on expiry columns for efficient cleanup queries

Revision ID: 2026_02_06_expiry_indexes
Revises: 2026_02_05_byok_encrypted
Create Date: 2026-02-06

Adds indexes on APIKey.expires_at and UserInvitation.expires_at to
improve performance of periodic cleanup jobs that scan for expired records.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "2026_02_06_expiry_indexes"
down_revision: Union[str, None] = "2026_02_05_byok_encrypted"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def index_exists(index_name: str) -> bool:
    """Check if an index exists (idempotent helper)."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    # Check all tables for the index
    for table_name in inspector.get_table_names():
        indexes = inspector.get_indexes(table_name)
        if any(idx["name"] == index_name for idx in indexes):
            return True
    return False


def upgrade() -> None:
    """Add indexes on expiry columns."""
    if not index_exists("idx_api_keys_expires_at"):
        op.create_index(
            "idx_api_keys_expires_at",
            "api_keys",
            ["expires_at"],
        )

    if not index_exists("idx_user_invitations_expires_at"):
        op.create_index(
            "idx_user_invitations_expires_at",
            "user_invitations",
            ["expires_at"],
        )


def downgrade() -> None:
    """Remove expiry indexes."""
    if index_exists("idx_user_invitations_expires_at"):
        op.drop_index("idx_user_invitations_expires_at", table_name="user_invitations")

    if index_exists("idx_api_keys_expires_at"):
        op.drop_index("idx_api_keys_expires_at", table_name="api_keys")
