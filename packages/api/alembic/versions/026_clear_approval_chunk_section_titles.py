"""Clear the section titles stored in chat messages' approval payloads.

Each approval payload kept the offered chunks with their ``section_title``,
and titles used to be stored and offered raw (the opening of a heading
line, values included). The titles are display-only, so every
``chunks[].section_title`` in ``chat_messages.approval_data`` is set to
null; chunk texts and every other field are left as they are. Rows are
read and written in id batches through SQLAlchemy's JSON type, so the
same code runs on SQLite and PostgreSQL. A re-run finds nothing left to
clear. Only counts are logged.

Downgrade keeps the change: the removed titles cannot be restored.

Revision ID: 026
Revises: 025
"""

import logging

import sqlalchemy as sa
from alembic import op

revision = "026"
down_revision = "025"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

_BATCH = 500
_MESSAGES = sa.table(
    "chat_messages",
    sa.column("id", sa.Integer),
    sa.column("approval_data", sa.JSON),
)


def _without_titles(payload):
    """Return ``payload`` with every chunk title cleared, or ``None`` when none was set."""
    if not isinstance(payload, dict) or not isinstance(payload.get("chunks"), list):
        return None
    changed = False
    chunks = []
    for chunk in payload["chunks"]:
        if isinstance(chunk, dict) and chunk.get("section_title") is not None:
            chunk = {**chunk, "section_title": None}
            changed = True
        chunks.append(chunk)
    return {**payload, "chunks": chunks} if changed else None


def clear_titles(conn: sa.Connection) -> int:
    """Clear every stored chunk title; return the number of rows rewritten."""
    changed, after = 0, 0
    while True:
        rows = conn.execute(
            sa.select(_MESSAGES.c.id, _MESSAGES.c.approval_data)
            .where(_MESSAGES.c.id > after, _MESSAGES.c.approval_data.isnot(None))
            .order_by(_MESSAGES.c.id)
            .limit(_BATCH)
        ).all()
        if not rows:
            return changed
        after = rows[-1][0]
        for row_id, payload in rows:
            cleared = _without_titles(payload)
            if cleared is not None:
                conn.execute(
                    _MESSAGES.update()
                    .where(_MESSAGES.c.id == row_id)
                    .values(approval_data=cleared)
                )
                changed += 1


def upgrade() -> None:
    changed = clear_titles(op.get_bind())
    logger.info("Cleared approval chunk section titles in %d chat message(s)", changed)


def downgrade() -> None:
    """Keep the change: the cleared titles cannot be restored."""
