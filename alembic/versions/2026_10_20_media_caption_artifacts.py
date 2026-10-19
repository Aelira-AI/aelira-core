"""Allow media scans to publish generated caption artifacts for review."""

from alembic import op

revision = "20261020_media_captions"
down_revision = "20261018_media_truth"
branch_labels = None
depends_on = None

TABLE = "remediation_artifacts"
CONSTRAINT = "ck_remediation_artifacts_scan_type"
DOCUMENT_TYPES = (
    "'PDF', 'POWERPOINT', 'WORD', 'EXCEL', 'LATEX', "
    "'IMAGE', 'WEBSITE', 'CANVAS_CONTENT'"
)


def upgrade() -> None:
    op.drop_constraint(CONSTRAINT, TABLE, type_="check")
    op.create_check_constraint(
        CONSTRAINT,
        TABLE,
        f"scan_type IN ({DOCUMENT_TYPES}, 'VIDEO', 'MULTIMEDIA')",
    )


def downgrade() -> None:
    # The earlier schema cannot hold media caption artifacts. Make reconciliation
    # explicit rather than deleting reviewed or published caption files.
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM remediation_artifacts "
        "WHERE scan_type IN ('VIDEO', 'MULTIMEDIA')) "
        "THEN RAISE EXCEPTION 'Media caption artifacts require reconciliation before downgrade'; "
        "END IF; END $$;"
    )
    op.drop_constraint(CONSTRAINT, TABLE, type_="check")
    op.create_check_constraint(CONSTRAINT, TABLE, f"scan_type IN ({DOCUMENT_TYPES})")
