"""Add waitlist_signups table

Revision ID: 2026_01_06_waitlist
Revises: 2025_11_30_phase4
Create Date: 2026-01-06 12:00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

# revision identifiers, used by Alembic.
revision: str = "2026_01_06_waitlist"
down_revision: Union[str, None] = "2025_11_30_phase4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Check if table exists to avoid errors if it was created manually
    conn = op.get_bind()
    inspector = Inspector.from_engine(conn)
    tables = inspector.get_table_names()

    if "waitlist_signups" not in tables:
        op.create_table(
            "waitlist_signups",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("email", sa.String(length=255), nullable=False),
            sa.Column("newsletter", sa.Boolean(), nullable=True, default=False),
            sa.Column("source", sa.String(length=255), nullable=True),
            sa.Column("metadata", sa.JSON(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=True,
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("email"),
        )


def downgrade() -> None:
    op.drop_table("waitlist_signups")
