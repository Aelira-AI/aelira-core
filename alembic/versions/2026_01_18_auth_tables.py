"""add authentication tables (magic_links, user_sessions) and user auth columns

Revision ID: 2026_01_18_auth
Revises: 2026_01_18_unsubscribe
Create Date: 2026-01-18

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "2026_01_18_auth"
down_revision: Union[str, None] = "2026_01_18_unsubscribe"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create AuthProvider enum type
    auth_provider_enum = sa.Enum(
        "magic_link", "google", "microsoft", "api_key", name="authprovider"
    )
    auth_provider_enum.create(op.get_bind(), checkfirst=True)

    # Create magic_links table
    op.create_table(
        "magic_links",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, index=True),
        sa.Column("token_hash", sa.String(255), unique=True, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(512), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )
    op.create_index("idx_magic_links_email", "magic_links", ["email"])
    op.create_index(
        "idx_magic_links_token_hash", "magic_links", ["token_hash"], unique=True
    )
    op.create_index("idx_magic_links_expires_at", "magic_links", ["expires_at"])

    # Create user_sessions table
    op.create_table(
        "user_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("refresh_token_hash", sa.String(255), unique=True, nullable=False),
        sa.Column("access_token_jti", sa.String(36), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(512), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )
    op.create_index("idx_user_sessions_user_id", "user_sessions", ["user_id"])
    op.create_index(
        "idx_user_sessions_refresh_token_hash",
        "user_sessions",
        ["refresh_token_hash"],
        unique=True,
    )
    op.create_index("idx_user_sessions_expires_at", "user_sessions", ["expires_at"])

    # Add new columns to users table
    op.add_column(
        "users",
        sa.Column(
            "email_verified", sa.Boolean(), nullable=False, server_default="false"
        ),
    )
    op.add_column(
        "users",
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("microsoft_id", sa.String(255), nullable=True, unique=True),
    )
    op.add_column(
        "users",
        sa.Column("auth_provider", auth_provider_enum, nullable=True),
    )

    # Make google_id nullable (was NOT NULL before)
    op.alter_column(
        "users",
        "google_id",
        existing_type=sa.String(255),
        nullable=True,
    )

    # Create indexes for new user columns
    op.create_index("idx_users_microsoft_id", "users", ["microsoft_id"], unique=True)
    op.create_index("idx_users_auth_provider", "users", ["auth_provider"])

    # Grandfather existing users: set email_verified = true and auth_provider = 'google'
    op.execute("""
        UPDATE users
        SET email_verified = true,
            auth_provider = 'google'
        WHERE google_id IS NOT NULL
    """)

    # Set default auth_provider for any users without google_id
    op.execute("""
        UPDATE users
        SET auth_provider = 'magic_link'
        WHERE auth_provider IS NULL
    """)


def downgrade() -> None:
    # Drop indexes
    op.drop_index("idx_users_auth_provider", table_name="users")
    op.drop_index("idx_users_microsoft_id", table_name="users")

    # Remove columns from users table
    op.drop_column("users", "auth_provider")
    op.drop_column("users", "microsoft_id")
    op.drop_column("users", "email_verified_at")
    op.drop_column("users", "email_verified")

    # Make google_id NOT NULL again (may fail if there are users without google_id)
    op.alter_column(
        "users",
        "google_id",
        existing_type=sa.String(255),
        nullable=False,
    )

    # Drop user_sessions table
    op.drop_index("idx_user_sessions_expires_at", table_name="user_sessions")
    op.drop_index("idx_user_sessions_refresh_token_hash", table_name="user_sessions")
    op.drop_index("idx_user_sessions_user_id", table_name="user_sessions")
    op.drop_table("user_sessions")

    # Drop magic_links table
    op.drop_index("idx_magic_links_expires_at", table_name="magic_links")
    op.drop_index("idx_magic_links_token_hash", table_name="magic_links")
    op.drop_index("idx_magic_links_email", table_name="magic_links")
    op.drop_table("magic_links")

    # Drop the enum type
    auth_provider_enum = sa.Enum(
        "magic_link", "google", "microsoft", "api_key", name="authprovider"
    )
    auth_provider_enum.drop(op.get_bind(), checkfirst=True)
