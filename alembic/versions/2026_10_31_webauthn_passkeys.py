"""Add WebAuthn credentials table and passkey AuthProvider enum

Revision ID: 20261031_webauthn_passkeys
Revises: 20261028_webhook_secret
Create Date: 2026-10-31

Adds support for WebAuthn passkeys (FIDO2 / biometrics / hardware keys)
including a webauthn_credentials table with user relationship, foreign keys,
indices, and the 'passkey' enum value in authprovider.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

# revision identifiers, used by Alembic.
revision: str = "20261031_webauthn_passkeys"
down_revision: Union[str, None] = "20261028_webhook_secret"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_NAME = "webauthn_credentials"


def table_exists(table_name: str) -> bool:
    """Check if a table exists (idempotent helper)."""
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    # 1. Add 'passkey' value to authprovider enum for PostgreSQL.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE authprovider ADD VALUE IF NOT EXISTS 'passkey'")

    # 2. Create webauthn_credentials table.
    if not table_exists(TABLE_NAME):
        op.create_table(
            TABLE_NAME,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "user_id",
                sa.String(36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("credential_id", sa.String(512), unique=True, nullable=False),
            sa.Column("public_key", sa.LargeBinary(), nullable=False),
            sa.Column("sign_count", sa.Integer(), server_default="0", nullable=False),
            sa.Column("transports", sa.JSON(), nullable=True),
            sa.Column("aaguid", sa.String(36), nullable=True),
            sa.Column("friendly_name", sa.String(100), server_default="Passkey", nullable=False),
            sa.Column("backup_eligible", sa.Boolean(), server_default=sa.false(), nullable=False),
            sa.Column("backup_state", sa.Boolean(), server_default=sa.false(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_webauthn_credentials_user_id", TABLE_NAME, ["user_id"])
        op.create_index("ix_webauthn_credentials_credential_id", TABLE_NAME, ["credential_id"])


def downgrade() -> None:
    if table_exists(TABLE_NAME):
        op.drop_index("ix_webauthn_credentials_credential_id", table_name=TABLE_NAME)
        op.drop_index("ix_webauthn_credentials_user_id", table_name=TABLE_NAME)
        op.drop_table(TABLE_NAME)
