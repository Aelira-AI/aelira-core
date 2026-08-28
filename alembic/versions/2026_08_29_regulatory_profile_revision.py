"""Add optimistic revision and custom-deadline verification timestamp.

Revision ID: 20260829_reg_profile_rev
Revises: 20260828_visual_contracts
"""

from alembic import op
import sqlalchemy as sa

revision = "20260829_reg_profile_rev"
down_revision = "20260828_visual_contracts"
branch_labels = None
depends_on = None

_TABLE = "departments"
_REVISION_COLUMN = "regulatory_profile_revision"
_VERIFIED_AT_COLUMN = "custom_deadline_verified_at"


def _column_names() -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(_TABLE)}


def upgrade() -> None:
    columns = _column_names()
    if _REVISION_COLUMN not in columns:
        op.add_column(
            _TABLE,
            sa.Column(
                _REVISION_COLUMN,
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
        )
    if _VERIFIED_AT_COLUMN not in columns:
        op.add_column(
            _TABLE,
            sa.Column(_VERIFIED_AT_COLUMN, sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    columns = _column_names()
    selected = [
        column
        for column in (_VERIFIED_AT_COLUMN, _REVISION_COLUMN)
        if column in columns
    ]
    if not selected:
        return
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(_TABLE, recreate="always") as batch_op:
            for column in selected:
                batch_op.drop_column(column)
    else:
        for column in selected:
            op.drop_column(_TABLE, column)
