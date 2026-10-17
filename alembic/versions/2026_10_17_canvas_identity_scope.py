"""Keep cloud file uniqueness while allowing distinct Canvas course content."""

from alembic import op
import sqlalchemy as sa

revision = "20261017_canvas_scope"
down_revision = "20260928_pdf_edit_pub"
branch_labels = None
depends_on = None

_TABLE = "cloud_files"
_OLD_CONSTRAINT = "uq_cloud_files_dept_provider_file"
_NON_CANVAS_INDEX = "uq_cloud_files_non_canvas_file_identity"


def upgrade() -> None:
    # The original constraint treats Canvas IDs from different content types as
    # the same file. Keep its uniqueness for other providers before removing it.
    op.create_index(
        _NON_CANVAS_INDEX,
        _TABLE,
        ["department_id", "provider", "provider_file_id"],
        unique=True,
        postgresql_where=sa.text("provider <> 'canvas'"),
    )
    op.drop_constraint(_OLD_CONSTRAINT, _TABLE, type_="unique")


def downgrade() -> None:
    # A Canvas course scan may have legitimately stored the same native ID in
    # two content types. Restoring the old constraint requires reconciliation.
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM cloud_files
                GROUP BY department_id, provider, provider_file_id
                HAVING COUNT(*) > 1
            ) THEN
                RAISE EXCEPTION
                    'Cloud file identities conflict with the old uniqueness rule; reconcile before downgrading';
            END IF;
        END
        $$;
        """)
    op.create_unique_constraint(
        _OLD_CONSTRAINT,
        _TABLE,
        ["department_id", "provider", "provider_file_id"],
    )
    op.drop_index(_NON_CANVAS_INDEX, table_name=_TABLE)
