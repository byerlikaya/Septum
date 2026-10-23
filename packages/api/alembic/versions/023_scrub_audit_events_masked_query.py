"""Remove the ``masked_query`` key from audit events' ``extra``.

Chat events kept the masked question, and masking is only as good as
detection, so a missed entity sat raw in the audit store. Only the
top-level key is removed, a re-run is a no-op, and downgrade keeps the
scrub (the text cannot be restored). Same approach as revision 022, copied
so revisions stay frozen; see its docstring for the PostgreSQL details.

Revision ID: 023
Revises: 022
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "023"
down_revision = "022"
branch_labels = None
depends_on = None

_KEY = "masked_query"
_BATCH = 1000
# Text search only: the key may also sit inside a string value, which the
# Python check below tells apart.
_POSTGRES_CANDIDATES = (
    "SELECT id, CAST(extra AS TEXT) FROM audit_events "
    f"WHERE id > :after AND strpos(CAST(extra AS TEXT), '\"{_KEY}\"') > 0 "
    "ORDER BY id LIMIT :batch"
)
_POSTGRES_UPDATE = "UPDATE audit_events SET extra = CAST(:extra AS json) WHERE id = :id"
_SQLITE = (
    "UPDATE audit_events SET extra = json_remove(extra, '$.masked_query') "
    "WHERE CASE WHEN json_valid(extra) THEN json_type(extra, '$.masked_query') END IS NOT NULL"
)
# Passes that may still change rows (one per duplicate ``masked_query`` key in a single row);
# one more pass confirms nothing is left.
_SQLITE_MAX_PASSES = 100


class _Raw(str):
    """Text written out unchanged: a number token's source text or punctuation."""


def _dump(value) -> str:
    """Serialize iteratively (explicit stack), so depth is bounded by the parser only."""
    out: list[str] = []
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, _Raw):
            out.append(item)
        elif isinstance(item, dict):
            parts: list = [_Raw("{")]
            for i, (key, val) in enumerate(item.items()):
                parts += [_Raw((", " if i else "") + json.dumps(key) + ": "), val]
            stack.extend(reversed(parts + [_Raw("}")]))
        elif isinstance(item, list):
            parts = [_Raw("[")]
            for i, val in enumerate(item):
                parts += [_Raw(", "), val] if i else [val]
            stack.extend(reversed(parts + [_Raw("]")]))
        else:
            out.append(json.dumps(item))
    return "".join(out)


def _without_key(raw: str) -> str | None:
    """Return ``raw`` re-serialized without the key, or ``None`` if it has none."""
    value = json.loads(raw, parse_float=_Raw, parse_int=_Raw, parse_constant=_Raw)
    if not isinstance(value, dict) or _KEY not in value:
        return None
    del value[_KEY]
    return _dump(value)


def scrub_postgres(conn: sa.Connection) -> int:
    """Drop the key on PostgreSQL; return the number of rows rewritten."""
    changed, after = 0, 0
    while True:
        rows = conn.execute(
            sa.text(_POSTGRES_CANDIDATES), {"after": after, "batch": _BATCH}
        ).all()
        if not rows:
            return changed
        after = rows[-1][0]
        updates = []
        for row_id, raw in rows:
            try:
                scrubbed = _without_key(raw)
            except RecursionError:
                raise RuntimeError(
                    f"audit event {row_id}: extra is nested too deeply for the json "
                    "parser; inspect or repair this row, then re-run the upgrade"
                ) from None
            if scrubbed is not None:
                updates.append({"id": row_id, "extra": scrubbed})
        if updates:
            conn.execute(sa.text(_POSTGRES_UPDATE), updates)
            changed += len(updates)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        scrub_postgres(bind)
    else:
        for _ in range(_SQLITE_MAX_PASSES + 1):
            if bind.execute(sa.text(_SQLITE)).rowcount == 0:
                return
        raise RuntimeError(
            f"audit_events still hold masked_query after {_SQLITE_MAX_PASSES} scrub passes; "
            "inspect or repair the rows, then re-run the upgrade"
        )


def downgrade() -> None:
    """Keep the scrub: the removed text cannot be restored."""
