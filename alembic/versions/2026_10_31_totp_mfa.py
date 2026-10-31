"""Add totp_credentials table for authenticator app MFA.

Revision ID: 20261031_totp_mfa
Revises: 20261031_webauthn_passkeys
Create Date: 2026-10-31
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261031_totp_mfa"
down_revision = "20261031_webauthn_passkeys"
branch_labels = None
depends_on = None


def table_exists(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not table_exists("totp_credentials"):
        op.create_table(
            "totp_credentials",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.String(length=36), nullable=False),
            sa.Column("encrypted_secret", sa.Text(), nullable=False),
            sa.Column(
                "is_confirmed",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column(
                "backup_codes",
                sa.JSON().with_variant(
                    postgresql.JSONB(astext_type=sa.Text()), "postgresql"
                ),
                nullable=True,
            ),
            sa.Column(
                "friendly_name",
                sa.String(length=100),
                nullable=False,
                server_default="Authenticator App",
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(
                ["user_id"],
                ["users.id"],
                name="fk_totp_credentials_user_id",
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", name="uq_totp_credentials_user_id"),
        )
        op.create_index(
            "ix_totp_credentials_user_id",
            "totp_credentials",
            ["user_id"],
            unique=True,
        )


def downgrade() -> None:
    if table_exists("totp_credentials"):
        op.drop_index(
            "ix_totp_credentials_user_id",
            table_name="totp_credentials",
        )
        op.drop_table("totp_credentials")
