"""Add sanitized_languages to documents.

The distinct language codes of the segments the pipeline masked the
document with, as a JSON list: the dominant language first (the value of
``sanitized_language``), then the remaining segment languages by
descending share of the text. Nullable with no default and no backfill:
existing rows stay NULL, which means the document was masked before
per-segment detection (legacy, not segmented).

The upgrade checks the live schema first, so it is a no-op on a database
whose ``documents`` table already has the column (built by ``create_all``).
Downgrade drops the column and the lists stored in it.

Revision ID: 028
Revises: 027
"""

import sqlalchemy as sa
from alembic import op

revision = "028"
down_revision = "027"
branch_labels = None
depends_on = None


def _existing_columns(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "sanitized_languages" not in _existing_columns("documents"):
        op.add_column(
            "documents",
            sa.Column("sanitized_languages", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    with op.batch_alter_table("documents") as batch_op:
        batch_op.drop_column("sanitized_languages")
