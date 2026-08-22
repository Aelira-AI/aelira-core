"""Add explicit department-scoped LMS AI policy.

Revision ID: 20260820_lms_ai_policy
Revises: 20260819_alert_schedule
"""

from alembic import op
import sqlalchemy as sa

revision = "20260820_lms_ai_policy"
down_revision = "20260819_alert_schedule"
branch_labels = None
depends_on = None

TABLE = "departments"
PROVIDER_CHECK = "ck_departments_lms_ai_provider"
PURPOSES_CHECK = "ck_departments_lms_ai_purposes"
CONSISTENCY_CHECK = "ck_departments_lms_ai_policy_consistency"


def upgrade():
    # Non-null server defaults make this safe for every existing department.
    op.add_column(
        TABLE,
        sa.Column(
            "lms_ai_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        TABLE, sa.Column("lms_ai_provider", sa.String(length=50), nullable=True)
    )
    op.add_column(
        TABLE,
        sa.Column(
            "lms_ai_purposes",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.create_check_constraint(
        PROVIDER_CHECK,
        TABLE,
        "lms_ai_provider IS NULL OR lms_ai_provider IN "
        "('ollama', 'gemini', 'openai', 'anthropic', 'xai')",
    )
    op.create_check_constraint(
        PURPOSES_CHECK,
        TABLE,
        "jsonb_typeof(lms_ai_purposes::jsonb) = 'array' AND "
        'lms_ai_purposes::jsonb <@ \'["remediation", "alt_text"]\'::jsonb AND '
        "jsonb_array_length(lms_ai_purposes::jsonb) = ("
        "CASE WHEN lms_ai_purposes::jsonb @> "
        "'[\"remediation\"]'::jsonb THEN 1 ELSE 0 END + "
        "CASE WHEN lms_ai_purposes::jsonb @> "
        "'[\"alt_text\"]'::jsonb THEN 1 ELSE 0 END)",
    )
    op.create_check_constraint(
        CONSISTENCY_CHECK,
        TABLE,
        "(NOT lms_ai_enabled AND lms_ai_provider IS NULL AND "
        "lms_ai_purposes::jsonb = '[]'::jsonb) OR "
        "(lms_ai_enabled AND lms_ai_provider IS NOT NULL AND "
        "jsonb_array_length(lms_ai_purposes::jsonb) > 0)",
    )


def downgrade():
    op.drop_constraint(CONSISTENCY_CHECK, TABLE, type_="check")
    op.drop_constraint(PURPOSES_CHECK, TABLE, type_="check")
    op.drop_constraint(PROVIDER_CHECK, TABLE, type_="check")
    op.drop_column(TABLE, "lms_ai_purposes")
    op.drop_column(TABLE, "lms_ai_provider")
    op.drop_column(TABLE, "lms_ai_enabled")
