"""Add durable weekly-summary schedule and delivery fences.

Revision ID: 20260830_weekly_summary
Revises: 20260829_ai_provider_cfg
"""

from alembic import op
import sqlalchemy as sa

revision = "20260830_weekly_summary"
down_revision = "20260829_ai_provider_cfg"
branch_labels = None
depends_on = None

_TABLE = "cloud_job_queue"
_OLD_EFFECT_CHECK = "ck_cloud_job_queue_external_effect_upload_only"
_EFFECT_CHECK = "ck_cloud_job_queue_external_effect_owned"
_WINDOW_INDEX = "uq_cloud_job_queue_weekly_summary_window"


def upgrade() -> None:
    op.drop_constraint(_OLD_EFFECT_CHECK, _TABLE, type_="check")
    op.create_check_constraint(
        _EFFECT_CHECK,
        _TABLE,
        "job_type IN ('upload', 'weekly_summary') OR "
        "(external_effect_state IS NULL AND external_effect_token IS NULL AND "
        "external_effect_started_at IS NULL)",
    )
    op.create_index(
        _WINDOW_INDEX,
        _TABLE,
        ["department_id", "job_type", "dedupe_key"],
        unique=True,
        postgresql_where=sa.text(
            "job_type = 'weekly_summary' AND dedupe_key IS NOT NULL"
        ),
        sqlite_where=sa.text("job_type = 'weekly_summary' AND dedupe_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(_WINDOW_INDEX, table_name=_TABLE)
    op.drop_constraint(_EFFECT_CHECK, _TABLE, type_="check")
    op.execute(
        sa.text(
            "UPDATE cloud_job_queue SET external_effect_state = NULL, "
            "external_effect_token = NULL, external_effect_started_at = NULL "
            "WHERE job_type = 'weekly_summary'"
        )
    )
    op.create_check_constraint(
        _OLD_EFFECT_CHECK,
        _TABLE,
        "job_type = 'upload' OR (external_effect_state IS NULL AND "
        "external_effect_token IS NULL AND external_effect_started_at IS NULL)",
    )
