"""Allow incomplete media scans to retain findings without inventing a score."""

from alembic import op
import sqlalchemy as sa

revision = "20261018_media_truth"
down_revision = "20261017_canvas_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "scan_results", "compliance_score", existing_type=sa.Float(), nullable=True
    )
    op.alter_column(
        "compliance_snapshots",
        "avg_compliance_score",
        existing_type=sa.Float(),
        nullable=True,
        server_default=None,
    )
    op.add_column(
        "compliance_snapshots",
        sa.Column("metric_provenance", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    # A legacy non-nullable schema cannot represent the retained partial scans.
    # Make reconciliation explicit; never replace unknown with zero or delete rows.
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM scan_results WHERE compliance_score IS NULL) "
        "THEN RAISE EXCEPTION 'Unassessed scan scores require reconciliation before downgrade'; "
        "END IF; END $$;"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM compliance_snapshots WHERE metric_provenance IS NOT NULL) "
        "THEN RAISE EXCEPTION 'Versioned compliance snapshots require reconciliation before downgrade'; "
        "END IF; END $$;"
    )
    op.drop_column("compliance_snapshots", "metric_provenance")
    op.alter_column(
        "scan_results", "compliance_score", existing_type=sa.Float(), nullable=False
    )
