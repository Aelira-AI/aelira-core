"""Add workspace-owned AI provider configuration.

Revision ID: 20260829_ai_provider_cfg
Revises: 20260829_reg_profile_rev
"""

from alembic import op
import sqlalchemy as sa


revision = "20260829_ai_provider_cfg"
down_revision = "20260829_reg_profile_rev"
branch_labels = None
depends_on = None

_PROVIDERS = "'ollama', 'gemini', 'openai', 'anthropic', 'xai'"
_TABLE = "department_ai_provider_configs"


def upgrade() -> None:
    op.add_column(
        "departments",
        sa.Column("ai_primary_provider", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "departments",
        sa.Column("ai_fallback_provider", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "departments",
        sa.Column(
            "ai_provider_config_revision",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )

    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("departments", recreate="always") as batch_op:
            batch_op.create_check_constraint(
                "ck_departments_ai_primary_provider",
                f"ai_primary_provider IS NULL OR ai_primary_provider IN ({_PROVIDERS})",
            )
            batch_op.create_check_constraint(
                "ck_departments_ai_fallback_provider",
                f"ai_fallback_provider IS NULL OR ai_fallback_provider IN ({_PROVIDERS})",
            )
            batch_op.create_check_constraint(
                "ck_departments_ai_provider_config_revision",
                "ai_provider_config_revision >= 0",
            )
            batch_op.create_check_constraint(
                "ck_departments_ai_provider_selection_distinct",
                "ai_primary_provider IS NULL OR ai_fallback_provider IS NULL OR "
                "ai_primary_provider <> ai_fallback_provider",
            )
    else:
        op.create_check_constraint(
            "ck_departments_ai_primary_provider",
            "departments",
            f"ai_primary_provider IS NULL OR ai_primary_provider IN ({_PROVIDERS})",
        )
        op.create_check_constraint(
            "ck_departments_ai_fallback_provider",
            "departments",
            f"ai_fallback_provider IS NULL OR ai_fallback_provider IN ({_PROVIDERS})",
        )
        op.create_check_constraint(
            "ck_departments_ai_provider_config_revision",
            "departments",
            "ai_provider_config_revision >= 0",
        )
        op.create_check_constraint(
            "ck_departments_ai_provider_selection_distinct",
            "departments",
            "ai_primary_provider IS NULL OR ai_fallback_provider IS NULL OR "
            "ai_primary_provider <> ai_fallback_provider",
        )

    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("department_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("api_key_encrypted", sa.Text(), nullable=True),
        sa.Column("text_model", sa.String(length=128), nullable=True),
        sa.Column("code_model", sa.String(length=128), nullable=True),
        sa.Column("vision_model", sa.String(length=128), nullable=True),
        sa.Column(
            "configured_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            f"provider IN ({_PROVIDERS})",
            name="ck_department_ai_provider_configs_provider",
        ),
        sa.CheckConstraint(
            "(provider = 'ollama' AND api_key_encrypted IS NULL) OR "
            "(provider <> 'ollama' AND api_key_encrypted IS NOT NULL)",
            name="ck_department_ai_provider_configs_credential",
        ),
        sa.ForeignKeyConstraint(
            ["department_id"], ["departments.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "department_id",
            "provider",
            name="uq_department_ai_provider_configs_department_provider",
        ),
    )
    op.create_index(
        "ix_department_ai_provider_configs_department_id",
        _TABLE,
        ["department_id"],
        unique=False,
    )

    # Preserve only unambiguous legacy state. Selection remains null, and the
    # migration never decrypts, probes, or guesses unknown providers.
    op.execute(
        sa.text(
            f"""
            INSERT INTO {_TABLE} (
                id, department_id, provider, api_key_encrypted, configured_at, updated_at
            )
            SELECT
                id, id, byok_provider, byok_api_key_encrypted,
                COALESCE(byok_configured_at, CURRENT_TIMESTAMP), CURRENT_TIMESTAMP
            FROM departments
            WHERE
                (byok_provider = 'ollama' AND byok_api_key_encrypted IS NULL)
                OR (
                    byok_provider IN ('gemini', 'openai', 'anthropic', 'xai')
                    AND byok_api_key_encrypted IS NOT NULL
                )
            """
        )
    )
    # The new table is the only credential authority after this migration.
    # Remove copied legacy ciphertext so rotation or deletion cannot leave an
    # older usable secret behind.
    op.execute(
        sa.text(
            f"""
            UPDATE departments
            SET byok_provider = NULL,
                byok_api_key_encrypted = NULL,
                byok_configured_at = NULL
            WHERE id IN (SELECT department_id FROM {_TABLE})
            """
        )
    )


def downgrade() -> None:
    # Restore the primary provider, or the sole configured provider, into the
    # legacy single-provider fields before removing the authoritative table.
    op.execute(
        sa.text(
            f"""
            UPDATE departments
            SET byok_provider = (
                    SELECT provider FROM {_TABLE}
                    WHERE department_id = departments.id
                    ORDER BY CASE
                        WHEN provider = departments.ai_primary_provider THEN 0 ELSE 1
                    END, provider
                    LIMIT 1
                ),
                byok_api_key_encrypted = (
                    SELECT api_key_encrypted FROM {_TABLE}
                    WHERE department_id = departments.id
                    ORDER BY CASE
                        WHEN provider = departments.ai_primary_provider THEN 0 ELSE 1
                    END, provider
                    LIMIT 1
                ),
                byok_configured_at = (
                    SELECT configured_at FROM {_TABLE}
                    WHERE department_id = departments.id
                    ORDER BY CASE
                        WHEN provider = departments.ai_primary_provider THEN 0 ELSE 1
                    END, provider
                    LIMIT 1
                )
            WHERE ai_primary_provider IS NOT NULL
               OR 1 = (
                    SELECT COUNT(*) FROM {_TABLE}
                    WHERE department_id = departments.id
                )
            """
        )
    )
    op.drop_index(
        "ix_department_ai_provider_configs_department_id", table_name=_TABLE
    )
    op.drop_table(_TABLE)

    constraint_names = (
        "ck_departments_ai_provider_selection_distinct",
        "ck_departments_ai_provider_config_revision",
        "ck_departments_ai_fallback_provider",
        "ck_departments_ai_primary_provider",
    )
    column_names = (
        "ai_provider_config_revision",
        "ai_fallback_provider",
        "ai_primary_provider",
    )
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("departments", recreate="always") as batch_op:
            for name in constraint_names:
                batch_op.drop_constraint(name, type_="check")
            for name in column_names:
                batch_op.drop_column(name)
    else:
        for name in constraint_names:
            op.drop_constraint(name, "departments", type_="check")
        for name in column_names:
            op.drop_column("departments", name)
