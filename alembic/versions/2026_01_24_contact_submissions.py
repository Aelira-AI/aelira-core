"""Add contact_submissions table for CRM integration

Revision ID: 2026_01_24_contact
Revises: 02062a6b19f1
Create Date: 2026-01-24 12:00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

# revision identifiers, used by Alembic.
revision: str = "2026_01_24_contact"
down_revision: Union[str, None] = "02062a6b19f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Check if table exists to avoid errors if it was created manually
    conn = op.get_bind()
    inspector = Inspector.from_engine(conn)
    tables = inspector.get_table_names()

    if "contact_submissions" not in tables:
        op.create_table(
            "contact_submissions",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("email", sa.String(length=255), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("organization", sa.String(length=255), nullable=True),
            sa.Column("role", sa.String(length=255), nullable=True),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("source", sa.String(length=255), nullable=True),
            sa.Column("metadata", sa.JSON(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=True,
            ),
            # CRM sync tracking
            sa.Column("crm_synced", sa.Boolean(), nullable=False, default=False),
            sa.Column("crm_synced_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("crm_person_id", sa.String(length=36), nullable=True),
            # Response tracking
            sa.Column("responded", sa.Boolean(), nullable=False, default=False),
            sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("responded_by", sa.String(length=255), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        # Create index on email for faster lookups
        op.create_index(
            "ix_contact_submissions_email", "contact_submissions", ["email"]
        )


def downgrade() -> None:
    op.drop_index("ix_contact_submissions_email", table_name="contact_submissions")
    op.drop_table("contact_submissions")
