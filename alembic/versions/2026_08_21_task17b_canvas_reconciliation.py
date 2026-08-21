"""Add leased Canvas reconciliation metadata.

Revision ID: 20260821_task17b_reconcile
Revises: 20260821_task17b_orphan
"""

from alembic import op
import sqlalchemy as sa

revision = "20260821_task17b_reconcile"
down_revision = "20260821_task17b_orphan"
branch_labels = None
depends_on = None


def upgrade() -> None:
    table = "content_writeback_log"
    op.drop_constraint("ck_content_writeback_log_reconciliation", table, type_="check")
    op.add_column(
        table,
        sa.Column(
            "reconciliation_attempt_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(table, sa.Column("reconciliation_lease_token", sa.String(36)))
    op.add_column(
        table, sa.Column("reconciliation_leased_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        table,
        sa.Column("reconciliation_lease_expires_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        table,
        sa.Column("reconciliation_next_attempt_at", sa.DateTime(timezone=True)),
    )
    op.add_column(table, sa.Column("reconciliation_last_error", sa.String(128)))
    op.add_column(
        table, sa.Column("reconciliation_resolved_at", sa.DateTime(timezone=True))
    )
    op.add_column(table, sa.Column("reconciliation_resolution", sa.String(32)))
    op.create_check_constraint(
        "ck_content_writeback_log_reconciliation",
        table,
        "reconciliation_status IS NULL OR reconciliation_status IN "
        "('pending', 'committed', 'reconciliation_required', 'reconciled', "
        "'failed_manual', 'manual_required')",
    )
    op.create_check_constraint(
        "ck_content_writeback_log_reconciliation_lease",
        table,
        "(reconciliation_lease_token IS NULL AND reconciliation_leased_at IS NULL "
        "AND reconciliation_lease_expires_at IS NULL) OR "
        "(reconciliation_lease_token IS NOT NULL AND reconciliation_leased_at IS NOT NULL "
        "AND reconciliation_lease_expires_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_content_writeback_log_reconciliation_attempts",
        table,
        "reconciliation_attempt_count >= 0",
    )
    op.create_check_constraint(
        "ck_content_writeback_log_reconciliation_resolution",
        table,
        "reconciliation_resolution IS NULL OR reconciliation_resolution IN "
        "('confirmed', 'failed_manual', 'manual_required')",
    )
    op.create_index(
        "ix_content_writeback_log_reconciliation_due",
        table,
        ["reconciliation_status", "reconciliation_next_attempt_at"],
    )


def downgrade() -> None:
    table = "content_writeback_log"
    op.execute(
        sa.text(
            "UPDATE content_writeback_log SET reconciliation_status = "
            "'reconciliation_required' WHERE reconciliation_status IN "
            "('reconciled', 'failed_manual', 'manual_required')"
        )
    )
    op.drop_index("ix_content_writeback_log_reconciliation_due", table_name=table)
    for constraint in (
        "ck_content_writeback_log_reconciliation_resolution",
        "ck_content_writeback_log_reconciliation_attempts",
        "ck_content_writeback_log_reconciliation_lease",
        "ck_content_writeback_log_reconciliation",
    ):
        op.drop_constraint(constraint, table, type_="check")
    for column in (
        "reconciliation_resolution",
        "reconciliation_resolved_at",
        "reconciliation_last_error",
        "reconciliation_next_attempt_at",
        "reconciliation_lease_expires_at",
        "reconciliation_leased_at",
        "reconciliation_lease_token",
        "reconciliation_attempt_count",
    ):
        op.drop_column(table, column)
    op.create_check_constraint(
        "ck_content_writeback_log_reconciliation",
        table,
        "reconciliation_status IS NULL OR reconciliation_status IN "
        "('pending', 'committed', 'reconciliation_required')",
    )
