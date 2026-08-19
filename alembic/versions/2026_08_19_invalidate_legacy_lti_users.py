"""Invalidate users provisioned by legacy permissive LTI authorization.

Existing LTI users may have been created before staff-only authorization claims
were required. Deactivation makes every legacy token fail closed; an approved
staff relaunch reactivates the matching user and mints a version 2 token.

Revision ID: 20260819_lti_reauth
Revises: 2026_08_18_canvas_content
"""

from alembic import op
import sqlalchemy as sa

revision = "20260819_lti_reauth"
down_revision = "2026_08_18_canvas_content"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users",
        sa.Column(
            "lti_reauthorization_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index("ix_api_keys_key_prefix", "api_keys", ["key_prefix"])
    op.execute(
        "UPDATE api_keys SET is_active = false " "WHERE key_prefix = 'aelira_live_'"
    )
    op.execute(
        "UPDATE api_keys SET is_active = false "
        "WHERE user_id IN (SELECT id FROM users WHERE auth_provider = 'lti')"
    )
    op.execute(
        "UPDATE users SET is_active = false, "
        "lti_reauthorization_required = true "
        "WHERE auth_provider = 'lti' AND is_active = true"
    )


def downgrade():
    # Reverse schema only. Data invalidation is intentionally irreversible:
    # rollback cannot prove which users or keys are safe to reactivate.
    op.drop_index("ix_api_keys_key_prefix", table_name="api_keys")
    op.drop_column("users", "lti_reauthorization_required")
