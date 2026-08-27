"""Add durable page-raster equation region provenance.

Revision ID: 20260828_region_provenance
Revises: 20260827_admin_handoff
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260828_region_provenance"
down_revision = "20260827_admin_handoff"
branch_labels = None
depends_on = None

_TABLE = "scan_fixes"
_COLUMN = "source_locator"
_CONSTRAINT = "ck_scan_fixes_source_locator"
_LOCATOR_JSON = sa.JSON().with_variant(JSONB(), "postgresql")


def _column_names() -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(_TABLE)}


def _check_names() -> set[str]:
    return {
        constraint["name"]
        for constraint in sa.inspect(op.get_bind()).get_check_constraints(_TABLE)
        if constraint.get("name")
    }


def upgrade() -> None:
    if _COLUMN not in _column_names():
        op.add_column(_TABLE, sa.Column(_COLUMN, _LOCATOR_JSON, nullable=True))
    if op.get_bind().dialect.name != "sqlite" and _CONSTRAINT not in _check_names():
        op.create_check_constraint(
            _CONSTRAINT,
            _TABLE,
            "source_locator IS NULL OR source_kind = 'image_equation'",
        )


def downgrade() -> None:
    if _CONSTRAINT in _check_names():
        op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    if _COLUMN in _column_names():
        op.drop_column(_TABLE, _COLUMN)
