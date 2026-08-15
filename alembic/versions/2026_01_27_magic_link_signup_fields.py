"""Add signup profile fields to magic_links

Revision ID: 2026_01_27_magic_link_signup
Revises: 2026_01_27_double_opt_in
Create Date: 2026-01-27

Stores name and institution on the magic link record so they can be
applied when the account is auto-created during verification.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "2026_01_27_magic_link_signup"
down_revision: Union[str, None] = "2026_01_27_double_opt_in"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def column_exists(table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table."""
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [col["name"] for col in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    if not column_exists("magic_links", "signup_name"):
        op.add_column(
            "magic_links", sa.Column("signup_name", sa.String(100), nullable=True)
        )

    if not column_exists("magic_links", "signup_institution"):
        op.add_column(
            "magic_links",
            sa.Column("signup_institution", sa.String(200), nullable=True),
        )


def downgrade() -> None:
    if column_exists("magic_links", "signup_institution"):
        op.drop_column("magic_links", "signup_institution")

    if column_exists("magic_links", "signup_name"):
        op.drop_column("magic_links", "signup_name")
