"""Add stable document identity and nullable snapshot compliance scores.

Revision ID: 20260828_scan_document_identity
Revises: 20260828_issue_tenant_repair
"""

from alembic import op
import sqlalchemy as sa

revision = "20260828_scan_document_identity"
down_revision = "20260828_issue_tenant_repair"
branch_labels = None
depends_on = None

_TABLE = "scans"
_COLUMN = "document_id"
_INDEX = "ix_scans_document_id"
_SOURCE_COLUMN = "document_source"
_SNAPSHOT_TABLE = "compliance_snapshots"
_SNAPSHOT_SCORE_COLUMN = "avg_compliance_score"

_BACKFILL_DOCUMENT_ID = """
WITH provider_evidence AS (
    SELECT
        cloud_files.department_id AS department_id,
        cloud_files.last_scan_id AS scan_id,
        cloud_files.id AS document_id
    FROM cloud_files
    WHERE cloud_files.last_scan_id IS NOT NULL

    UNION ALL

    SELECT
        cloud_job_queue.department_id AS department_id,
        cloud_job_queue.result_data ->> 'scan_id' AS scan_id,
        cloud_job_queue.cloud_file_id AS document_id
    FROM cloud_job_queue
    JOIN cloud_files
      ON cloud_files.id = cloud_job_queue.cloud_file_id
     AND cloud_files.department_id = cloud_job_queue.department_id
    WHERE cloud_job_queue.cloud_file_id IS NOT NULL
      AND cloud_job_queue.job_type IN ('scan', 'remediate', 'canvas_content')
      AND cloud_job_queue.result_data ->> 'scan_id' IS NOT NULL

    UNION ALL

    SELECT
        remediation_artifacts.department_id AS department_id,
        remediation_artifacts.scan_id AS scan_id,
        remediation_artifacts.cloud_file_id AS document_id
    FROM remediation_artifacts
    JOIN cloud_files
      ON cloud_files.id = remediation_artifacts.cloud_file_id
     AND cloud_files.department_id = remediation_artifacts.department_id
    WHERE remediation_artifacts.cloud_file_id IS NOT NULL
),
unambiguous_evidence AS (
    SELECT
        department_id,
        scan_id,
        MIN(document_id) AS document_id
    FROM provider_evidence
    WHERE scan_id IS NOT NULL
    GROUP BY department_id, scan_id
    HAVING COUNT(DISTINCT document_id) = 1
)
UPDATE scans
SET document_id = (
        SELECT unambiguous_evidence.document_id
        FROM unambiguous_evidence
        WHERE unambiguous_evidence.scan_id = scans.id
          AND unambiguous_evidence.department_id = scans.department_id
    ),
    document_source = 'cloud_file'
WHERE EXISTS (
      SELECT 1
      FROM unambiguous_evidence
      WHERE unambiguous_evidence.scan_id = scans.id
        AND unambiguous_evidence.department_id = scans.department_id
  )
"""


def _column_names() -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(_TABLE)}


def _index_names() -> set[str]:
    return {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes(_TABLE)
        if index.get("name")
    }


def _snapshot_score_column() -> dict | None:
    inspector = sa.inspect(op.get_bind())
    if _SNAPSHOT_TABLE not in inspector.get_table_names():
        return None
    return next(
        (
            column
            for column in inspector.get_columns(_SNAPSHOT_TABLE)
            if column["name"] == _SNAPSHOT_SCORE_COLUMN
        ),
        None,
    )


def _set_snapshot_score_nullable(nullable: bool) -> None:
    """Change snapshot score nullability without inventing a measured score."""

    column = _snapshot_score_column()
    if column is None or bool(column["nullable"]) is nullable:
        return

    if not nullable:
        # The prior schema required a value and treated 0 as its default. Restore
        # that invariant before reinstating NOT NULL during downgrade.
        op.execute(
            sa.text(
                "UPDATE compliance_snapshots "
                "SET avg_compliance_score = 0 "
                "WHERE avg_compliance_score IS NULL"
            )
        )

    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(_SNAPSHOT_TABLE, recreate="always") as batch_op:
            batch_op.alter_column(
                _SNAPSHOT_SCORE_COLUMN,
                existing_type=column["type"],
                nullable=nullable,
            )
    else:
        op.alter_column(
            _SNAPSHOT_TABLE,
            _SNAPSHOT_SCORE_COLUMN,
            existing_type=column["type"],
            nullable=nullable,
        )


def upgrade() -> None:
    if _COLUMN not in _column_names():
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(length=36), nullable=True))
    if _SOURCE_COLUMN not in _column_names():
        op.add_column(
            _TABLE, sa.Column(_SOURCE_COLUMN, sa.String(length=32), nullable=True)
        )
    if _INDEX not in _index_names():
        op.create_index(_INDEX, _TABLE, [_COLUMN], unique=False)

    # Link only provider attempts backed by durable, tenant-aligned evidence.
    # Historical failures are no longer CloudFile.last_scan_id, but their scan
    # job result or remediation artifact can still prove the document identity.
    # Ambiguous evidence remains NULL rather than assigning the wrong document.
    op.execute(sa.text(_BACKFILL_DOCUMENT_ID))

    # A department with enrolled but unverified documents has no measured
    # average. Persist NULL so daily history does not fabricate a 0% score.
    _set_snapshot_score_nullable(True)


def downgrade() -> None:
    _set_snapshot_score_nullable(False)
    if _INDEX in _index_names():
        op.drop_index(_INDEX, table_name=_TABLE)
    if _SOURCE_COLUMN in _column_names():
        op.drop_column(_TABLE, _SOURCE_COLUMN)
    if _COLUMN in _column_names():
        op.drop_column(_TABLE, _COLUMN)
