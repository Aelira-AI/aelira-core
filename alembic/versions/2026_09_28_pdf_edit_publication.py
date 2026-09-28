"""Persist reviewed PDF edit publication preconditions and provenance.

Revision ID: 20260928_pdf_edit_pub
Revises: 20260917_unknown_confidence
"""

from alembic import op
import sqlalchemy as sa

revision = "20260928_pdf_edit_pub"
down_revision = "20260917_unknown_confidence"
branch_labels = None
depends_on = None

_TABLE = "remediation_artifacts"
_PAIR = "ck_remediation_artifacts_edit_pair"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column("edit_precondition", sa.JSON(none_as_null=True), nullable=True),
    )
    op.add_column(
        _TABLE, sa.Column("edit_provenance", sa.JSON(none_as_null=True), nullable=True)
    )
    op.create_check_constraint(
        _PAIR,
        _TABLE,
        "(edit_precondition IS NULL AND edit_provenance IS NULL) OR "
        "(edit_precondition IS NOT NULL AND edit_provenance IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(_PAIR, _TABLE, type_="check")
    op.drop_column(_TABLE, "edit_provenance")
    op.drop_column(_TABLE, "edit_precondition")
