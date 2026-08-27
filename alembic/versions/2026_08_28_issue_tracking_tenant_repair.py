"""Repair historical issue-tracking tenant references.

Revision ID: 20260828_issue_tenant_repair
Revises: 20260828_region_provenance
"""

from alembic import op
import sqlalchemy as sa

revision = "20260828_issue_tenant_repair"
down_revision = "20260828_region_provenance"
branch_labels = None
depends_on = None


_MISSCOPED_ISSUES = """
SELECT COUNT(*)
FROM issue_tracking AS issue
JOIN scans AS scan ON scan.id = issue.scan_id
WHERE issue.department_id <> scan.department_id
"""

_ALIGN_ISSUE_DEPARTMENTS = """
UPDATE issue_tracking
SET department_id = (
    SELECT scan.department_id
    FROM scans AS scan
    WHERE scan.id = issue_tracking.scan_id
)
WHERE EXISTS (
    SELECT 1
    FROM scans AS scan
    WHERE scan.id = issue_tracking.scan_id
      AND scan.department_id <> issue_tracking.department_id
)
"""


def _mismatched_user_count(column: str) -> sa.TextClause:
    return sa.text(f"""
SELECT COUNT(*)
FROM issue_tracking AS issue
WHERE issue.{column} IS NOT NULL
  AND NOT EXISTS (
      SELECT 1
      FROM users AS account
      WHERE account.id = issue.{column}
        AND account.department_id = issue.department_id
  )
""")


def _clear_mismatched_user(column: str) -> sa.TextClause:
    return sa.text(f"""
UPDATE issue_tracking
SET {column} = NULL
WHERE {column} IS NOT NULL
  AND NOT EXISTS (
      SELECT 1
      FROM users AS account
      WHERE account.id = issue_tracking.{column}
        AND account.department_id = issue_tracking.department_id
  )
""")


def upgrade() -> None:
    bind = op.get_bind()

    if bind.execute(sa.text(_MISSCOPED_ISSUES)).scalar_one():
        bind.execute(sa.text(_ALIGN_ISSUE_DEPARTMENTS))

    for column in ("assigned_to", "assigned_by", "resolved_by"):
        if bind.execute(_mismatched_user_count(column)).scalar_one():
            bind.execute(_clear_mismatched_user(column))


def downgrade() -> None:
    # Tenant repairs discard invalid references and cannot be reconstructed safely.
    pass
