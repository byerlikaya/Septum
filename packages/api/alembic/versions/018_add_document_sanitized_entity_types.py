"""Add sanitized_entity_types to documents.

The sorted list of entity type names the document was sanitized with,
recorded by the ingestion pipeline so the API can tell when the current
policy covers types the stored chunks were never masked for. Nullable with
no default and no backfill: existing rows stay NULL, which readers treat as
an unknown (stale) policy snapshot.

The upgrade checks the live schema first, so it is a no-op on a database
whose ``documents`` table already has the column (built by ``create_all``).
Downgrade drops the column and the snapshots stored in it.

Revision ID: 018
Revises: 017
"""

import sqlalchemy as sa
from alembic import op

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if not _has_column("documents", "sanitized_entity_types"):
        op.add_column(
            "documents",
            sa.Column("sanitized_entity_types", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    with op.batch_alter_table("documents") as batch_op:
        batch_op.drop_column("sanitized_entity_types")
