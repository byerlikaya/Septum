"""Remove the ``document_name`` key from audit events' ``extra``.

Filenames can carry personal data; audit events now name documents by id
only and the audit router resolves the name at read time. Only rows still
holding the key are rewritten, every other key is kept, and a re-run is a
no-op. The removed names cannot be restored, so downgrade keeps the scrub.

PostgreSQL rewrites rows as ``json``, never through ``jsonb``, so the other
keys keep their order and text. None of its json functions can read a
``\\u0000`` escape, so such rows are rewritten in Python instead. SQLite
skips rows whose ``extra`` is not valid JSON rather than aborting.

Revision ID: 022
Revises: 021
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "022"
down_revision = "021"
branch_labels = None
depends_on = None

_HAS_NUL_ESCAPE = r"strpos(extra::text, E'\\u0000') > 0"
_HOLDS_KEY = "json_typeof(extra) = 'object' AND extra -> 'document_name' IS NOT NULL"
_REBUILD = (
    "UPDATE audit_events SET extra = COALESCE("
    "(SELECT json_object_agg(e.key, e.value) FROM json_each(extra) AS e "
    "WHERE e.key <> 'document_name'), '{}'::json) "
)
_POSTGRES = _REBUILD + f"WHERE {_HOLDS_KEY}"
# CASE fixes the evaluation order, so json operators never read a NUL row.
_POSTGRES_SKIPPING_NUL = _REBUILD + f"WHERE CASE WHEN {_HAS_NUL_ESCAPE} THEN false ELSE {_HOLDS_KEY} END"
# Every NUL row, with the key or not: any of them breaks the unguarded UPDATE.
_POSTGRES_NUL_ROWS = f"SELECT id, extra::text FROM audit_events WHERE {_HAS_NUL_ESCAPE}"
_SQLITE = (
    "UPDATE audit_events SET extra = json_remove(extra, '$.document_name') "
    "WHERE CASE WHEN json_valid(extra) THEN json_type(extra, '$.document_name') END IS NOT NULL"
)


def scrub_postgres(conn: sa.Connection) -> int:
    """Drop the key on PostgreSQL; return the number of rows rewritten."""
    nul_rows = conn.execute(sa.text(_POSTGRES_NUL_ROWS)).all()
    if not nul_rows:
        return conn.execute(sa.text(_POSTGRES)).rowcount
    changed = conn.execute(sa.text(_POSTGRES_SKIPPING_NUL)).rowcount
    for row_id, raw in nul_rows:
        value = json.loads(raw)
        if not isinstance(value, dict) or "document_name" not in value:
            continue
        del value["document_name"]
        conn.execute(
            sa.text("UPDATE audit_events SET extra = CAST(:extra AS json) WHERE id = :id"),
            {"extra": json.dumps(value, ensure_ascii=False), "id": row_id},
        )
        changed += 1
    return changed


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        scrub_postgres(bind)
    else:
        op.execute(sa.text(_SQLITE))


def downgrade() -> None:
    """Keep the scrub: the removed filenames cannot be restored."""
