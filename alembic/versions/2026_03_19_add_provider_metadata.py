"""Add provider_metadata column to cloud_oauth_credentials

Revision ID: 2026_03_19_provider_metadata
Revises: 2026_03_03_widen_location
Create Date: 2026-03-19

Stores provider-specific metadata (e.g., canvas_instance_url) as JSON.
The 'metadata' attribute name is reserved in SQLAlchemy Declarative,
so the Python attribute is 'provider_metadata' and the DB column is
'provider_metadata'.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

# revision identifiers, used by Alembic.
revision: str = "2026_03_19_provider_metadata"
down_revision: Union[str, None] = "2026_03_03_widen_location"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def column_exists(table_name: str, column_name: str) -> bool:
    """Check if a column exists (idempotent helper)."""
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    columns = [c["name"] for c in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    if not column_exists("cloud_oauth_credentials", "provider_metadata"):
        op.add_column(
            "cloud_oauth_credentials",
            sa.Column("provider_metadata", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    if column_exists("cloud_oauth_credentials", "provider_metadata"):
        op.drop_column("cloud_oauth_credentials", "provider_metadata")
