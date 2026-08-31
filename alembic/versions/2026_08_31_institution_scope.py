"""Add stable institution scope for cross-department analytics.

Revision ID: 20260831_institution_scope
Revises: 20260830_cvd_metrics
"""

from uuid import NAMESPACE_URL, uuid5

from alembic import op
import sqlalchemy as sa

revision = "20260831_institution_scope"
down_revision = "20260830_cvd_metrics"
branch_labels = None
depends_on = None

_TABLE = "departments"
_COLUMN = "institution_scope_id"
_INDEX = "ix_departments_institution_scope_id"


def _column_names() -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(_TABLE)}


def _index_names() -> set[str]:
    return {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes(_TABLE)
        if index.get("name")
    }


def upgrade() -> None:
    connection = op.get_bind()
    if _COLUMN not in _column_names():
        op.add_column(
            _TABLE,
            sa.Column(
                _COLUMN,
                sa.String(length=36),
                nullable=True,
                server_default=(
                    sa.text("gen_random_uuid()::text")
                    if connection.dialect.name == "postgresql"
                    else None
                ),
            ),
        )

    departments = connection.execute(
        sa.text("SELECT id, institution FROM departments ORDER BY id")
    ).mappings()
    for department in departments:
        canonical_name = str(department["institution"] or "").strip().lower()
        scope_id = str(uuid5(NAMESPACE_URL, f"aelira-institution:{canonical_name}"))
        connection.execute(
            sa.text(
                "UPDATE departments SET institution_scope_id = :scope_id "
                "WHERE id = :department_id AND institution_scope_id IS NULL"
            ),
            {"scope_id": scope_id, "department_id": department["id"]},
        )

    if connection.dialect.name == "sqlite":
        with op.batch_alter_table(_TABLE, recreate="always") as batch_op:
            batch_op.alter_column(
                _COLUMN,
                existing_type=sa.String(length=36),
                nullable=False,
            )
    else:
        op.alter_column(
            _TABLE,
            _COLUMN,
            existing_type=sa.String(length=36),
            nullable=False,
            server_default=sa.text("gen_random_uuid()::text"),
        )

    if _INDEX not in _index_names():
        op.create_index(_INDEX, _TABLE, [_COLUMN], unique=False)


def downgrade() -> None:
    if _INDEX in _index_names():
        op.drop_index(_INDEX, table_name=_TABLE)
    if _COLUMN in _column_names():
        op.drop_column(_TABLE, _COLUMN)
