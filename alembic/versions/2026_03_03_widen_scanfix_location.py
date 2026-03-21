"""Widen scan_fixes.location from varchar(255) to text

Revision ID: 2026_03_03_widen_location
Revises: 2026_03_02_review_tables
Create Date: 2026-03-03

Web scan locations store "{page_url} | {selector}" which can easily
exceed 255 characters for long URLs. Change to unbounded text.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

# revision identifiers, used by Alembic.
revision: str = "2026_03_03_widen_location"
down_revision: Union[str, None] = "2026_03_02_review_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def column_exists(table_name: str, column_name: str) -> bool:
    """Check if a column exists (idempotent helper)."""
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    columns = [c["name"] for c in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    if column_exists("scan_fixes", "location"):
        op.alter_column(
            "scan_fixes",
            "location",
            existing_type=sa.String(255),
            type_=sa.Text(),
            existing_nullable=True,
        )


def downgrade() -> None:
    if column_exists("scan_fixes", "location"):
        op.alter_column(
            "scan_fixes",
            "location",
            existing_type=sa.Text(),
            type_=sa.String(255),
            existing_nullable=True,
        )
