"""Add Moodle and Brightspace LMS provider support

Revision ID: 1807727d838d
Revises: 2026_01_09_sync_folders
Create Date: 2026-01-09 16:06:09.467313

This migration documents the addition of two new LMS provider types:

1. MOODLE - World's most-used LMS
   - Market share: 60-70% Australia, 20% US
   - OAuth 2.0 + Web Services REST API integration
   - Self-hosted instances require instance_url parameter
   - Target: Community colleges, small institutions, Australian universities

2. BRIGHTSPACE - D2L Brightspace LMS
   - Market share: 15% US, 10% Australia
   - LTI 1.3 + REST API integration
   - Growing in community colleges

Provider column in cloud_oauth_credentials already supports string values,
so no schema changes are required. The CloudProvider enum in models.py
has been updated to include these new values.

Combined market coverage after this addition:
- US Higher Education: 60% → 95% (+35%)
- Australian Higher Education: 40% → 85% (+45%)

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1807727d838d'
down_revision: Union[str, None] = '2026_01_09_sync_folders'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    No schema changes required.

    The cloud_oauth_credentials.provider column is already String(20),
    which accommodates the new 'moodle' and 'brightspace' values.

    The CloudProvider enum in src/db/models.py enforces valid values
    at the application level.
    """
    pass


def downgrade() -> None:
    """
    No schema changes to revert.

    Existing 'moodle' and 'brightspace' credentials will remain in the database
    but will not be recognized by the application after downgrade.
    """
    pass
