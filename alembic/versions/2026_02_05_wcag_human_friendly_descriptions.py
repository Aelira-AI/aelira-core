"""Add human-friendly description columns to wcag_guidelines

Revision ID: 2026_02_05_human_friendly
Revises: 02062a6b19f1
Create Date: 2026-02-05 12:00:00.000000

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "2026_02_05_human_friendly"
down_revision = "2026_01_28_account_deletion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add human-friendly description columns
    # human_issue: Plain language description of the accessibility issue
    # human_fixed: Plain language description of what was fixed
    # wcag_guidelines table is created by populate_wcag_knowledge_base.py, not Alembic
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "wcag_guidelines" not in inspector.get_table_names():
        return
    op.add_column("wcag_guidelines", sa.Column("human_issue", sa.Text(), nullable=True))
    op.add_column("wcag_guidelines", sa.Column("human_fixed", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("wcag_guidelines", "human_fixed")
    op.drop_column("wcag_guidelines", "human_issue")
