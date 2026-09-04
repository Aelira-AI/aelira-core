"""Add controlled review deferrals.

Revision ID: 20260905_review_deferrals
Revises: 20260831_institution_scope
"""

from alembic import op
import sqlalchemy as sa

revision = "20260905_review_deferrals"
down_revision = "20260831_institution_scope"
branch_labels = None
depends_on = None

_TABLE = "scan_fixes"
_INDEX = "idx_scan_fixes_deferral"
_CONSTRAINT = "ck_scan_fixes_deferral_state"
_COLUMN_NAMES = (
    "deferral_status",
    "deferral_owner",
    "deferral_reason",
    "deferral_expires_at",
    "deferral_created_at",
    "deferral_updated_at",
    "deferral_closed_at",
)
_CHECK = (
    "(deferral_status IS NULL AND deferral_owner IS NULL AND "
    "deferral_reason IS NULL AND deferral_expires_at IS NULL AND "
    "deferral_created_at IS NULL AND deferral_updated_at IS NULL AND "
    "deferral_closed_at IS NULL) OR "
    "(deferral_status IN ('active', 'revoked', 'resolved') AND "
    "length(trim(deferral_owner)) > 0 AND "
    "length(trim(deferral_reason)) > 0 AND "
    "deferral_expires_at IS NOT NULL AND deferral_created_at IS NOT NULL AND "
    "deferral_updated_at IS NOT NULL AND "
    "((deferral_status = 'active' AND deferral_closed_at IS NULL) OR "
    "(deferral_status IN ('revoked', 'resolved') AND "
    "deferral_closed_at IS NOT NULL)))"
)


def _column_names() -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(_TABLE)}


def _index_names() -> set[str]:
    return {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes(_TABLE)
        if index.get("name")
    }


def _columns() -> tuple[sa.Column, ...]:
    return (
        sa.Column("deferral_status", sa.String(length=20), nullable=True),
        sa.Column("deferral_owner", sa.String(length=255), nullable=True),
        sa.Column("deferral_reason", sa.Text(), nullable=True),
        sa.Column("deferral_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deferral_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deferral_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deferral_closed_at", sa.DateTime(timezone=True), nullable=True),
    )


def _constraint_names() -> set[str]:
    return {
        constraint["name"]
        for constraint in sa.inspect(op.get_bind()).get_check_constraints(_TABLE)
        if constraint.get("name")
    }


def upgrade() -> None:
    existing = _column_names()
    for column in _columns():
        if column.name not in existing:
            op.add_column(_TABLE, column)

    if _CONSTRAINT not in _constraint_names():
        if op.get_bind().dialect.name == "sqlite":
            with op.batch_alter_table(_TABLE, recreate="always") as batch_op:
                batch_op.create_check_constraint(_CONSTRAINT, _CHECK)
        else:
            op.create_check_constraint(_CONSTRAINT, _TABLE, _CHECK)

    if _INDEX not in _index_names():
        op.create_index(
            _INDEX,
            _TABLE,
            ["deferral_status", "deferral_expires_at"],
            unique=False,
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(_TABLE, recreate="always") as batch_op:
            if _INDEX in _index_names():
                batch_op.drop_index(_INDEX)
            if _CONSTRAINT in _constraint_names():
                batch_op.drop_constraint(_CONSTRAINT, type_="check")
            for column_name in reversed(_COLUMN_NAMES):
                if column_name in _column_names():
                    batch_op.drop_column(column_name)
        return

    if _INDEX in _index_names():
        op.drop_index(_INDEX, table_name=_TABLE)
    if _CONSTRAINT in _constraint_names():
        op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    for column_name in reversed(_COLUMN_NAMES):
        if column_name in _column_names():
            op.drop_column(_TABLE, column_name)
