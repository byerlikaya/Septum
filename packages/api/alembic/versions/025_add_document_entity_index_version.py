"""Add entity_index_version to documents.

The entity-index hashing scheme (value normalization and per-token
hashes) a document's ``entity_index`` rows were written under. Nullable
with no default and no backfill: existing rows stay NULL, which the
startup reindex treats as written under an older scheme and rebuilds
from the document's anonymization map. A migration cannot rebuild them
itself, because the maps are encrypted under a key only the app holds.

The upgrade checks the live schema first, so it is a no-op on a database
whose ``documents`` table already has the column (built by ``create_all``).
Downgrade drops the column; the next upgrade then reindexes every document.

Revision ID: 025
Revises: 024
"""

import sqlalchemy as sa
from alembic import op

revision = "025"
down_revision = "024"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if not _has_column("documents", "entity_index_version"):
        op.add_column(
            "documents",
            sa.Column("entity_index_version", sa.Integer(), nullable=True),
        )


def downgrade() -> None:
    with op.batch_alter_table("documents") as batch_op:
        batch_op.drop_column("entity_index_version")
