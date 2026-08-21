"""Enforce composite Canvas course-content identity."""

from alembic import op

revision = "20260822_task18_identity"
down_revision = "20260822_upload_effect_fence"
branch_labels = None
depends_on = None

INDEX_NAME = "uq_cloud_files_canvas_content_identity"


def upgrade() -> None:
    # Duplicate rows may carry different scans, artifacts, or writeback state.
    # Merging them automatically would be destructive and ambiguous, so fail
    # closed and require an operator-reviewed reconciliation before retrying.
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM cloud_files
                WHERE provider = 'canvas'
                  AND provider_parent_id IS NOT NULL
                GROUP BY
                    department_id,
                    provider,
                    provider_parent_id,
                    COALESCE(content_source, 'file'),
                    provider_file_id
                HAVING COUNT(*) > 1
            ) THEN
                RAISE EXCEPTION
                    'Duplicate Canvas composite identities exist in cloud_files; reconcile them before upgrading';
            END IF;
        END
        $$;
        """)
    op.execute(f"""
        CREATE UNIQUE INDEX {INDEX_NAME}
        ON cloud_files (
            department_id,
            provider,
            provider_parent_id,
            COALESCE(content_source, 'file'),
            provider_file_id
        )
        WHERE provider = 'canvas'
          AND provider_parent_id IS NOT NULL
        """)


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="cloud_files")
