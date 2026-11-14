"""Add ingestion_run_id to documents.

A token naming one ingestion claim on the document: each run that claims a
document for processing records its own id, and a run whose token no longer
matches is stale, so it cannot write over a newer claim. ``String(36)``
holds a UUID. Nullable with no default and no backfill: a document that is
already ``processing`` at upgrade time keeps NULL, which is intended, since
the startup orphan reset fails such documents anyway. No index: the token
is only compared on rows already looked up by primary key.

The upgrade checks the live schema first, so it is a no-op on a database
whose ``documents`` table already has the column (built by ``create_all``).
Downgrade drops the column only if present; it holds no data worth keeping
once no run reads it.

Revision ID: 030
Revises: 029
"""

import sqlalchemy as sa
from alembic import op

revision = "030"
down_revision = "029"
branch_labels = None
depends_on = None

_TABLE = "documents"
_COLUMN = "ingestion_run_id"


def _has_column() -> bool:
    return _COLUMN in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(_TABLE)}


def upgrade() -> None:
    if not _has_column():
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(length=36), nullable=True))


def downgrade() -> None:
    if _has_column():
        with op.batch_alter_table(_TABLE) as batch_op:
            batch_op.drop_column(_COLUMN)
