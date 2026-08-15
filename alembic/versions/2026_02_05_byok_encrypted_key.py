"""Add encrypted API key column for BYOK storage

Revision ID: 2026_02_05_byok_encrypted
Revises: 2026_02_05_human_friendly
Create Date: 2026-02-05

This migration adds the byok_api_key_encrypted column to departments table
for secure storage of department-provided API keys using Fernet encryption.

SECURITY NOTES:
- API keys are encrypted using Fernet symmetric encryption
- BYOK_ENCRYPTION_KEY env var must be set before using this feature
- See src/utils/encryption.py for encryption/decryption utilities
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "2026_02_05_byok_encrypted"
down_revision: Union[str, None] = "2026_02_05_human_friendly"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def column_exists(table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table (idempotent helper)."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [col["name"] for col in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    """Add byok_api_key_encrypted column to departments table."""
    if not column_exists("departments", "byok_api_key_encrypted"):
        op.add_column(
            "departments",
            sa.Column("byok_api_key_encrypted", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    """Remove byok_api_key_encrypted column from departments table."""
    if column_exists("departments", "byok_api_key_encrypted"):
        op.drop_column("departments", "byok_api_key_encrypted")
