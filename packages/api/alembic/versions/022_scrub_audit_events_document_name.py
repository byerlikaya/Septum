"""Remove the ``document_name`` key from audit events' ``extra``.

Filenames can carry personal data; audit events now name documents by id
only and the audit router resolves the name at read time. Only the
top-level ``document_name`` key is removed and only rows holding it are
rewritten, so a re-run is a no-op. The names cannot be restored, so
downgrade keeps the scrub.

PostgreSQL's json functions abort on escapes its text type cannot hold
(``\\u0000``, lone surrogates such as ``\\udc80``), so its rows are never read
through a json operator: a plain text search picks the candidates and
Python rewrites them. Key order and number tokens keep their exact text;
strings are written ASCII-escaped (as the app writes them), which keeps
them valid in any server encoding; duplicate keys collapse, last one wins.
A key spelled with escapes (``"document\\u005fname"``) is not matched by the
text search; the app never writes keys that way. A row nested deeper than
Python's json parser accepts (about 10,000 levels) aborts the upgrade,
naming the row id, rather than keeping its name. SQLite rewrites in place
with ``json_remove`` and skips rows whose ``extra`` is not valid JSON;
``json_remove`` drops only the first of duplicate keys, so it repeats until
no row changes.

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

_BATCH = 1000
# Text search only: the key may also sit inside a string value, which the
# Python check below tells apart.
_POSTGRES_CANDIDATES = (
    "SELECT id, CAST(extra AS TEXT) FROM audit_events "
    "WHERE id > :after AND strpos(CAST(extra AS TEXT), '\"document_name\"') > 0 "
    "ORDER BY id LIMIT :batch"
)
_POSTGRES_UPDATE = "UPDATE audit_events SET extra = CAST(:extra AS json) WHERE id = :id"
_SQLITE = (
    "UPDATE audit_events SET extra = json_remove(extra, '$.document_name') "
    "WHERE CASE WHEN json_valid(extra) THEN json_type(extra, '$.document_name') END IS NOT NULL"
)
# One pass per duplicate ``document_name`` key in a single row.
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
    if not isinstance(value, dict) or "document_name" not in value:
        return None
    del value["document_name"]
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
        for _ in range(_SQLITE_MAX_PASSES):
            if bind.execute(sa.text(_SQLITE)).rowcount == 0:
                return
        raise RuntimeError(
            f"audit_events still hold document_name after {_SQLITE_MAX_PASSES} scrub passes; "
            "inspect or repair the rows, then re-run the upgrade"
        )


def downgrade() -> None:
    """Keep the scrub: the removed filenames cannot be restored."""
