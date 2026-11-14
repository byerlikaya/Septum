"""Index chunks by (document_id, index).

``chunks.document_id`` is a foreign key with no index, and neither SQLite
nor PostgreSQL indexes one on its own, so every read, delete and IN check
by document scanned the whole table, the ON DELETE CASCADE from
``documents`` included. The composite leads with ``document_id``, so it
serves those equality and IN lookups, and its second column returns a
document's chunks already in ``index`` order, so the ordered per-document
reads need no sort.

A plain ``CREATE INDEX`` inside the migration transaction, not
``CONCURRENTLY``: migrations run at container start or from the setup flow,
so at worst chunk writes wait for the short build (a rolling deploy can keep
an old instance writing meanwhile), whereas a failed concurrent build would
leave an invalid index the existence check below would then skip.

The upgrade checks the live schema first, so it is a no-op on a database
that already has the index (built by ``create_all``). Downgrade drops it
only if present. No data is lost either way.

Revision ID: 029
Revises: 028
"""

import sqlalchemy as sa
from alembic import op

revision = "029"
down_revision = "028"
branch_labels = None
depends_on = None

_TABLE = "chunks"
_INDEX = "ix_chunks_document_id_index"


def _has_index() -> bool:
    return _INDEX in {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(_TABLE)}


def upgrade() -> None:
    if not _has_index():
        op.create_index(_INDEX, _TABLE, ["document_id", "index"])


def downgrade() -> None:
    if _has_index():
        op.drop_index(_INDEX, table_name=_TABLE)
