"""Add sanitized_language to documents.

The language code (ISO 639-1, or ISO 639-2/3 where no two-letter code
exists) the document pipeline actually masked the document with. Nullable
with no default and no backfill: existing rows stay NULL, which readers
treat as an unknown masking language that needs reprocessing.

The upgrade checks the live schema first, so it is a no-op on a database
whose ``documents`` table already has the column (built by ``create_all``).
Downgrade drops the column and the language codes stored in it.

Revision ID: 020
Revises: 019
"""

import sqlalchemy as sa
from alembic import op

revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if not _has_column("documents", "sanitized_language"):
        op.add_column(
            "documents",
            sa.Column("sanitized_language", sa.String(), nullable=True),
        )


def downgrade() -> None:
    with op.batch_alter_table("documents") as batch_op:
        batch_op.drop_column("sanitized_language")
