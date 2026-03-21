"""add LTI registrations table for multi-tenant LTI support

Revision ID: 2026_01_16_lti_reg
Revises: 2026_01_14_security
Create Date: 2026-01-16

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2026_01_16_lti_reg'
down_revision: Union[str, None] = '2026_01_14_security'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create LTI platform enum type if not exists
    conn = op.get_bind()
    conn.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE ltiplatform AS ENUM ('CANVAS', 'BLACKBOARD', 'MOODLE', 'BRIGHTSPACE', 'SAKAI', 'OTHER');
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """))

    # Create lti_registrations table using raw SQL to avoid SQLAlchemy enum issues
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS lti_registrations (
            id VARCHAR(36) PRIMARY KEY,
            department_id VARCHAR(36) NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
            platform ltiplatform NOT NULL,
            platform_name VARCHAR(255),
            issuer VARCHAR(512) NOT NULL,
            client_id VARCHAR(255) NOT NULL,
            deployment_id VARCHAR(255),
            auth_login_url VARCHAR(1024),
            auth_token_url VARCHAR(1024),
            jwks_url VARCHAR(1024),
            public_key_pem TEXT,
            private_key_pem TEXT,
            scopes JSONB,
            capabilities JSONB,
            is_active BOOLEAN DEFAULT TRUE,
            last_launch_at TIMESTAMP WITH TIME ZONE,
            launch_count INTEGER DEFAULT 0,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE
        );
    """))

    # Create indexes
    op.create_index(
        'idx_lti_registrations_lookup',
        'lti_registrations',
        ['platform', 'issuer', 'client_id'],
        unique=True
    )

    op.create_index(
        'idx_lti_registrations_department',
        'lti_registrations',
        ['department_id']
    )


def downgrade() -> None:
    op.drop_index('idx_lti_registrations_department', table_name='lti_registrations')
    op.drop_index('idx_lti_registrations_lookup', table_name='lti_registrations')
    op.drop_table('lti_registrations')

    # Drop enum type (only if no other tables use it)
    conn = op.get_bind()
    conn.execute(sa.text("DROP TYPE IF EXISTS ltiplatform"))
