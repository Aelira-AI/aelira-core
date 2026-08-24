"""Add durable image-equation evidence and review provenance.

Revision ID: 20260824_task8_review
Revises: 20260822_v095_job_quarantine
"""

from alembic import op
import sqlalchemy as sa

revision = "20260824_task8_review"
down_revision = "20260822_v095_job_quarantine"
branch_labels = None
depends_on = None

_TABLE = "scan_fixes"
_SOURCE_CONSTRAINT = "ck_scan_fixes_source_kind"


def _column_names() -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(_TABLE)}


def _check_names() -> set[str]:
    return {
        constraint["name"]
        for constraint in sa.inspect(op.get_bind()).get_check_constraints(_TABLE)
        if constraint.get("name")
    }


def upgrade() -> None:
    """Add nullable fields safely when replayed by recovery tooling."""
    existing = _column_names()
    additions = (
        sa.Column("provider_used", sa.String(64), nullable=True),
        sa.Column("source_kind", sa.String(32), nullable=True),
        sa.Column("verification_evidence", sa.JSON(), nullable=True),
    )
    for column in additions:
        if column.name not in existing:
            op.add_column(_TABLE, column)

    if (
        op.get_bind().dialect.name != "sqlite"
        and _SOURCE_CONSTRAINT not in _check_names()
    ):
        op.create_check_constraint(
            _SOURCE_CONSTRAINT,
            _TABLE,
            "source_kind IS NULL OR source_kind = 'image_equation'",
        )


def downgrade() -> None:
    """Remove only fields that still exist; safe for interrupted rollbacks."""
    if _SOURCE_CONSTRAINT in _check_names():
        op.drop_constraint(_SOURCE_CONSTRAINT, _TABLE, type_="check")

    existing = _column_names()
    for name in ("verification_evidence", "source_kind", "provider_used"):
        if name in existing:
            op.drop_column(_TABLE, name)
