"""Add typed visual-semantic review contracts and approval digests.

Revision ID: 20260828_visual_contracts
Revises: 20260828_deadline_profile
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260828_visual_contracts"
down_revision = "20260828_deadline_profile"
branch_labels = None
depends_on = None

_FIX_TABLE = "scan_fixes"
_ARTIFACT_TABLE = "remediation_artifacts"
_JSON = sa.JSON().with_variant(JSONB(), "postgresql")
_FIX_COLUMNS = (
    "visual_semantic_contract",
    "review_digest",
    "approved_review_digest",
)
_ARTIFACT_COLUMNS = ("approval_review_digest",)
_COLUMN_CONSTRAINTS = {
    "visual_semantic_contract": "ck_scan_fixes_visual_semantic_contract",
    "review_digest": "ck_scan_fixes_review_digest",
    "approved_review_digest": "ck_scan_fixes_approved_review_digest",
    "approval_review_digest": "ck_remediation_artifacts_approval_review_digest",
}


def _column_names(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _lower_hex_64_constraint(column: str) -> str:
    stripped = column
    for character in "0123456789abcdef":
        stripped = f"replace({stripped}, '{character}', '')"
    return f"length({column}) = 64 AND {column} = lower({column}) AND {stripped} = ''"


def _digest_column(name: str, constraint_name: str) -> sa.Column:
    return sa.Column(
        name,
        sa.String(length=64),
        sa.CheckConstraint(
            f"{name} IS NULL OR ({_lower_hex_64_constraint(name)})",
            name=constraint_name,
        ),
        nullable=True,
    )


def upgrade() -> None:
    fix_columns = _column_names(_FIX_TABLE)
    if "visual_semantic_contract" not in fix_columns:
        op.add_column(
            _FIX_TABLE,
            sa.Column(
                "visual_semantic_contract",
                _JSON,
                sa.CheckConstraint(
                    "visual_semantic_contract IS NULL OR "
                    "(source_kind IS NOT NULL AND source_kind = 'image_equation')",
                    name="ck_scan_fixes_visual_semantic_contract",
                ),
                nullable=True,
            ),
        )
    if "review_digest" not in fix_columns:
        op.add_column(
            _FIX_TABLE,
            _digest_column("review_digest", "ck_scan_fixes_review_digest"),
        )
    if "approved_review_digest" not in fix_columns:
        op.add_column(
            _FIX_TABLE,
            _digest_column(
                "approved_review_digest",
                "ck_scan_fixes_approved_review_digest",
            ),
        )

    artifact_columns = _column_names(_ARTIFACT_TABLE)
    if "approval_review_digest" not in artifact_columns:
        op.add_column(
            _ARTIFACT_TABLE,
            _digest_column(
                "approval_review_digest",
                "ck_remediation_artifacts_approval_review_digest",
            ),
        )


def _drop_columns(table: str, owned_columns: tuple[str, ...]) -> None:
    present = _column_names(table)
    selected = tuple(column for column in owned_columns if column in present)
    if not selected:
        return
    if op.get_bind().dialect.name == "sqlite":
        check_names = {
            constraint["name"]
            for constraint in sa.inspect(op.get_bind()).get_check_constraints(table)
            if constraint.get("name")
        }
        with op.batch_alter_table(table, recreate="always") as batch_op:
            for column in selected:
                constraint = _COLUMN_CONSTRAINTS[column]
                if constraint in check_names:
                    batch_op.drop_constraint(constraint, type_="check")
            for column in reversed(selected):
                batch_op.drop_column(column)
        return
    for column in reversed(selected):
        op.drop_column(table, column)


def downgrade() -> None:
    _drop_columns(_ARTIFACT_TABLE, _ARTIFACT_COLUMNS)
    _drop_columns(_FIX_TABLE, _FIX_COLUMNS)
