"""Store a per-subscription secret hash for Microsoft webhook clientState.

Revision ID: 20261028_webhook_secret
Revises: 20261020_media_captions

Microsoft Graph subscriptions sent the department ID as clientState, and the
webhook accepted any notification that echoed it. Each new subscription now
gets a random secret, and only its SHA-256 hash is stored here. This adds one
nullable column with no backfill: NULL marks a subscription created before
this change, whose notifications are refused until it is created again.

The change is additive. Downgrading leaves the column and its hashes in
place, because earlier releases ignore it and a later upgrade keeps the
secrets of subscriptions created in between.
"""

from alembic import op
import sqlalchemy as sa

revision = "20261028_webhook_secret"
down_revision = "20261020_media_captions"
branch_labels = None
depends_on = None

TABLE = "cloud_webhook_subscriptions"
COLUMN = "client_state_hash"


def column_exists() -> bool:
    return any(
        column["name"] == COLUMN
        for column in sa.inspect(op.get_bind()).get_columns(TABLE)
    )


def upgrade() -> None:
    if not column_exists():
        op.add_column(TABLE, sa.Column(COLUMN, sa.String(64), nullable=True))


def downgrade() -> None:
    # Nothing is dropped; see the module docstring.
    pass
