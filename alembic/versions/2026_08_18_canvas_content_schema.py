"""Canvas content columns, write-back audit table, WCAG knowledge base

The models grew these while the migrations did not, and nothing called
create_all outside the tests, so a database built the way a deployment
builds one (alembic upgrade head, from entrypoint.sh) came up without the
columns the Canvas content feature reads on every request. Anyone
installing from a clean database got a working application that failed the
moment it touched course content.

This migration is deliberately additive. Autogenerate proposed dropping
four tables that exist in older installations but not in this edition's
models; dropping a self-hoster's data to tidy a schema is not a migration,
it is an incident.

Revision ID: 2026_08_18_canvas_content
Revises: 2026_03_19_lti_auth
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision = "2026_08_18_canvas_content"
down_revision = "2026_03_19_lti_auth"
branch_labels = None
depends_on = None


CLOUD_FILE_COLUMNS = [
    sa.Column("content_source", sa.String(30), nullable=True),
    sa.Column("content_body", sa.Text(), nullable=True),
    sa.Column("content_slug", sa.String(255), nullable=True),
    sa.Column("content_updated_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("remediated_body", sa.Text(), nullable=True),
    sa.Column("remediated_compliance_score", sa.Float(), nullable=True),
    sa.Column("remediated_issues_fixed", sa.Integer(), nullable=True),
    sa.Column("remediated_issues_remaining", sa.Integer(), nullable=True),
    sa.Column("provider_metadata", sa.JSON(), nullable=True),
    sa.Column("writeback_status", sa.String(20), nullable=True),
    sa.Column("writeback_at", sa.DateTime(timezone=True), nullable=True),
]

ALERT_COLUMNS = [
    sa.Column("weekly_summary_day", sa.Integer(), nullable=True),
    sa.Column("weekly_summary_hour", sa.Integer(), nullable=True),
]


def _existing(table):
    bind = op.get_bind()
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def _tables():
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade():
    tables = _tables()

    # The scan-type enum never learned the values the models added. Without
    # these, recording a Canvas content scan fails at insert with "invalid
    # input value for enum scantype", so the columns above would have been
    # necessary but not sufficient. ADD VALUE is safe inside a transaction
    # on PostgreSQL 12 and later as long as the value is not used in the
    # same transaction, which it is not.
    for value in ("CANVAS_CONTENT", "MULTIMEDIA"):
        op.execute(f"ALTER TYPE scantype ADD VALUE IF NOT EXISTS '{value}'")

    # Columns are added only where absent: installations that predate the
    # migrations and grew these by other means must not fail on upgrade.
    if "cloud_files" in tables:
        have = _existing("cloud_files")
        for column in CLOUD_FILE_COLUMNS:
            if column.name not in have:
                op.add_column("cloud_files", column.copy())

    if "email_alert_settings" in tables:
        have = _existing("email_alert_settings")
        for column in ALERT_COLUMNS:
            if column.name not in have:
                op.add_column("email_alert_settings", column.copy())

    if "content_writeback_log" not in tables:
        op.create_table(
            "content_writeback_log",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "cloud_file_id",
                sa.String(36),
                sa.ForeignKey("cloud_files.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
            ),
            sa.Column("original_body", sa.Text(), nullable=False),
            sa.Column("remediated_body", sa.Text(), nullable=False),
            sa.Column("approved_by", sa.String(255), nullable=True),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("written_back_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("canvas_revision", sa.String(255), nullable=True),
            sa.Column("rollback_status", sa.String(20), nullable=True),
            sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=True),
        )

    if "wcag_guidelines" not in tables:
        op.create_table(
            "wcag_guidelines",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("rule_id", sa.String(50), nullable=False, unique=True),
            sa.Column("wcag_criterion", sa.String(20), nullable=False),
            sa.Column("wcag_level", sa.String(5), nullable=False),
            sa.Column(
                "wcag_version", sa.String(10), nullable=False, server_default="2.2"
            ),
            sa.Column("title", sa.Text(), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("principle", sa.String(50), nullable=False),
            sa.Column("guideline", sa.String(100), nullable=False),
            sa.Column("severity_criteria", JSONB(), nullable=False),
            sa.Column("business_impact_template", sa.Text(), nullable=True),
            sa.Column("technical_impact", sa.Text(), nullable=True),
            sa.Column("fix_examples", JSONB(), nullable=True),
            sa.Column("best_practices", ARRAY(sa.Text()), nullable=True),
            sa.Column("tags", ARRAY(sa.Text()), server_default="{}"),
            sa.Column("act_rule_ids", ARRAY(sa.Text()), nullable=True),
            sa.Column("related_rules", ARRAY(sa.Text()), nullable=True),
            sa.Column("embedding", JSONB(), nullable=True),
            sa.Column("human_issue", sa.Text(), nullable=True),
            sa.Column("human_fixed", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        )
        op.create_index("idx_wcag_rule_id", "wcag_guidelines", ["rule_id"])
        op.create_index("idx_wcag_criterion", "wcag_guidelines", ["wcag_criterion"])
        op.create_index("idx_wcag_level", "wcag_guidelines", ["wcag_level"])


def downgrade():
    # Enum values are deliberately not removed: PostgreSQL cannot drop one,
    # and rows may already reference them.
    tables = _tables()

    if "wcag_guidelines" in tables:
        op.drop_index("idx_wcag_level", table_name="wcag_guidelines")
        op.drop_index("idx_wcag_criterion", table_name="wcag_guidelines")
        op.drop_index("idx_wcag_rule_id", table_name="wcag_guidelines")
        op.drop_table("wcag_guidelines")

    if "content_writeback_log" in tables:
        op.drop_table("content_writeback_log")

    if "email_alert_settings" in tables:
        have = _existing("email_alert_settings")
        for column in ALERT_COLUMNS:
            if column.name in have:
                op.drop_column("email_alert_settings", column.name)

    if "cloud_files" in tables:
        have = _existing("cloud_files")
        for column in CLOUD_FILE_COLUMNS:
            if column.name in have:
                op.drop_column("cloud_files", column.name)
