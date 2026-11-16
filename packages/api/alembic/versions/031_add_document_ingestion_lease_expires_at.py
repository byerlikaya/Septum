"""Add ingestion_lease_expires_at to documents.

The moment the current ingestion claim on the document lapses unless the
run holding it renews it. A run that is still alive keeps pushing it
forward; a claim whose lease has passed belongs to a run that is gone, so
it can be failed without touching another worker's live run.
``DateTime(timezone=True)`` matches the other timestamps (revision 012).
Nullable with no default and no backfill: NULL reads as expired, so a
document already ``processing`` at upgrade time is treated as orphaned,
which is what the startup reset does today. No index: the column is only
read on rows already filtered by ingestion status.

The upgrade checks the live schema first, so it is a no-op on a database
whose ``documents`` table already has the column (built by ``create_all``).
Downgrade drops the column only if present; a lease is transient and holds
no data worth keeping once no run reads it.

Revision ID: 031
Revises: 030
"""

import sqlalchemy as sa
from alembic import op

revision = "031"
down_revision = "030"
branch_labels = None
depends_on = None

_TABLE = "documents"
_COLUMN = "ingestion_lease_expires_at"


def _has_column() -> bool:
    return _COLUMN in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(_TABLE)}


def upgrade() -> None:
    if not _has_column():
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    if _has_column():
        with op.batch_alter_table(_TABLE) as batch_op:
            batch_op.drop_column(_COLUMN)
