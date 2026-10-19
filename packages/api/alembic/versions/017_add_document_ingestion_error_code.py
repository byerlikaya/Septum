"""Add ingestion_error_code to documents.

A short, stable machine-readable code for a failed ingestion, stored next
to the human-readable ``ingestion_error`` text so clients can branch on the
failure kind without parsing messages. Nullable with no default: existing
rows stay NULL and no backfill runs.

The upgrade checks the live schema first, so it is a no-op on a database
whose ``documents`` table already has the column (built by ``create_all``).
Downgrade drops the column and the codes stored in it.

Revision ID: 017
Revises: 016
"""

import sqlalchemy as sa
from alembic import op

revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if not _has_column("documents", "ingestion_error_code"):
        op.add_column(
            "documents",
            sa.Column("ingestion_error_code", sa.String(64), nullable=True),
        )


def downgrade() -> None:
    with op.batch_alter_table("documents") as batch_op:
        batch_op.drop_column("ingestion_error_code")
