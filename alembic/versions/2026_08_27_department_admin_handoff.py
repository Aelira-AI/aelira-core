"""Add tenant-bound department administrator handoffs.

Revision ID: 20260827_admin_handoff
Revises: 20260825_canvas_queue
"""

from alembic import op
import sqlalchemy as sa

revision = "20260827_admin_handoff"
down_revision = "20260825_canvas_queue"
branch_labels = None
depends_on = None


def _lower_hex_64(column: str) -> str:
    stripped = column
    for character in "0123456789abcdef":
        stripped = f"replace({stripped}, '{character}', '')"
    return f"length({column}) = 64 AND {column} = lower({column}) AND {stripped} = ''"


def upgrade() -> None:
    op.execute("UPDATE user_invitations SET role = 'FACULTY' WHERE role IS NULL")
    op.execute("UPDATE user_invitations SET status = 'pending' WHERE status IS NULL")
    op.alter_column("user_invitations", "role", nullable=False)
    op.alter_column("user_invitations", "status", nullable=False)
    op.add_column(
        "user_invitations",
        sa.Column(
            "purpose",
            sa.String(50),
            nullable=False,
            server_default="member",
        ),
    )
    op.add_column(
        "user_invitations",
        sa.Column("delivery_queued_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_user_invitations_purpose",
        "user_invitations",
        "purpose IN ('member', 'department_admin_handoff')",
    )
    op.create_check_constraint(
        "ck_user_invitations_handoff_token_digest",
        "user_invitations",
        "purpose != 'department_admin_handoff' OR (" f"{_lower_hex_64('token')})",
    )
    op.create_check_constraint(
        "ck_user_invitations_handoff_admin_role",
        "user_invitations",
        "purpose != 'department_admin_handoff' OR (role IS NOT NULL AND role = 'ADMIN')",
    )
    op.create_check_constraint(
        "ck_user_invitations_handoff_normalized_email",
        "user_invitations",
        "purpose != 'department_admin_handoff' OR email = lower(trim(email))",
    )
    op.create_check_constraint(
        "ck_user_invitations_handoff_delivery_queued",
        "user_invitations",
        "purpose != 'department_admin_handoff' OR (status IS NOT NULL AND delivery_queued_at IS NOT NULL)",
    )
    op.create_index(
        "uq_user_invitations_department_admin_handoff",
        "user_invitations",
        ["department_id"],
        unique=True,
        postgresql_where=sa.text("purpose = 'department_admin_handoff'"),
    )
    op.create_index(
        "uq_user_invitations_admin_handoff_email",
        "user_invitations",
        ["email"],
        unique=True,
        postgresql_where=sa.text("purpose = 'department_admin_handoff'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_user_invitations_admin_handoff_email",
        table_name="user_invitations",
    )
    op.drop_index(
        "uq_user_invitations_department_admin_handoff",
        table_name="user_invitations",
    )
    op.drop_constraint(
        "ck_user_invitations_handoff_delivery_queued",
        "user_invitations",
        type_="check",
    )
    op.drop_constraint(
        "ck_user_invitations_handoff_normalized_email",
        "user_invitations",
        type_="check",
    )
    op.drop_constraint(
        "ck_user_invitations_handoff_admin_role",
        "user_invitations",
        type_="check",
    )
    op.drop_constraint(
        "ck_user_invitations_handoff_token_digest",
        "user_invitations",
        type_="check",
    )
    op.drop_constraint(
        "ck_user_invitations_purpose",
        "user_invitations",
        type_="check",
    )
    op.drop_column("user_invitations", "delivery_queued_at")
    op.drop_column("user_invitations", "purpose")
    op.alter_column("user_invitations", "status", nullable=True)
    op.alter_column("user_invitations", "role", nullable=True)
