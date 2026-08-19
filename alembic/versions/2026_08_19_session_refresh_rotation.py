"""Add state for atomic replay-tolerant refresh rotation.

Revision ID: 2026_08_19_session_refresh_rotation
Revises: 2026_08_19_invalidate_legacy_lti_users
"""

from alembic import op
import sqlalchemy as sa

revision = "2026_08_19_session_refresh_rotation"
down_revision = "2026_08_19_invalidate_legacy_lti_users"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "user_sessions",
        sa.Column("previous_refresh_token_hash", sa.String(255), nullable=True),
    )
    op.add_column(
        "user_sessions",
        sa.Column(
            "refresh_grace_expires_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.add_column(
        "user_sessions",
        sa.Column("refresh_replay_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "user_sessions",
        sa.Column("refresh_replay_ciphertext", sa.Text(), nullable=True),
    )


def downgrade():
    op.drop_column("user_sessions", "refresh_replay_ciphertext")
    op.drop_column("user_sessions", "refresh_replay_used_at")
    op.drop_column("user_sessions", "refresh_grace_expires_at")
    op.drop_column("user_sessions", "previous_refresh_token_hash")
