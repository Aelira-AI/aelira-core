"""Add explicit US Title II entity classification.

Revision ID: 20260828_deadline_profile
Revises: 20260828_scan_document_identity
"""

from alembic import op
import sqlalchemy as sa

revision = "20260828_deadline_profile"
down_revision = "20260828_scan_document_identity"
branch_labels = None
depends_on = None

_TABLE = "departments"
_COLUMN = "title_ii_entity_class"
_CHECK = "ck_departments_title_ii_entity_class"


def _column_names() -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(_TABLE)}


def upgrade() -> None:
    if _COLUMN in _column_names():
        return

    op.add_column(
        _TABLE,
        sa.Column(
            _COLUMN,
            sa.String(length=32),
            sa.CheckConstraint(
                "title_ii_entity_class IS NULL OR "
                "title_ii_entity_class IN ('large', 'small_or_special_district')",
                name=_CHECK,
            ),
            nullable=True,
        ),
    )

    # Preserve the deadline previously shown to legacy rows that relied on the
    # old ORM defaults. This runs only while adding the column; new incomplete
    # profiles remain NULL and fail closed.
    op.execute(
        sa.text(
            "UPDATE departments SET country_code = 'US', "
            "regulatory_framework = 'US_ADA_TITLE_II' "
            "WHERE country_code IS NULL AND regulatory_framework IS NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE departments SET regulatory_framework = 'US_ADA_TITLE_II' "
            "WHERE regulatory_framework IS NULL AND upper(country_code) = 'US'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE departments SET country_code = 'US' "
            "WHERE country_code IS NULL "
            "AND regulatory_framework = 'US_ADA_TITLE_II'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE departments SET title_ii_entity_class = 'large' "
            "WHERE regulatory_framework = 'US_ADA_TITLE_II'"
        )
    )


def downgrade() -> None:
    if _COLUMN not in _column_names():
        return
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(_TABLE, recreate="always") as batch_op:
            batch_op.drop_constraint(_CHECK, type_="check")
            batch_op.drop_column(_COLUMN)
    else:
        op.drop_column(_TABLE, _COLUMN)
