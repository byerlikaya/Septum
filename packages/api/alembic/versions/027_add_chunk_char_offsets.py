"""Add start_char and end_char to chunks.

The half-open span of a chunk's ``raw_text`` inside its document's joined
extracted text, so the preview can lay chunks out without repeating the
overlap between neighbours. Nullable with no default and no backfill:
existing chunks stay NULL, since the joined text is not stored and a
migration cannot rebuild it.

The upgrade checks the live schema first, so it is a no-op on a database
whose ``chunks`` table already has the columns (built by ``create_all``).
Downgrade drops both columns.

Revision ID: 027
Revises: 026
"""

import sqlalchemy as sa
from alembic import op

revision = "027"
down_revision = "026"
branch_labels = None
depends_on = None

_COLUMNS = ("start_char", "end_char")


def _existing_columns(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    existing = _existing_columns("chunks")
    for name in _COLUMNS:
        if name not in existing:
            op.add_column("chunks", sa.Column(name, sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("chunks") as batch_op:
        for name in reversed(_COLUMNS):
            batch_op.drop_column(name)
