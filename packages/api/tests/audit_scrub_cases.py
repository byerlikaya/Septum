"""Stored ``audit_events.extra`` texts and what each scrub must leave behind.

Shared by the SQLite startup scrub, Alembic revision 022 on SQLite and
revision 022 on PostgreSQL. Each row is ``(stored, postgres, sqlite)``: the
raw json text inserted, then the exact text expected after each engine's
scrub. PostgreSQL rewrites in Python (spaced separators, ASCII-escaped
strings); SQLite's ``json_remove`` writes compact text and keeps raw
characters. Rows without a top-level ``document_name`` stay byte-for-byte.
"""

from __future__ import annotations

_BIG_INT = "9" * 5000
_NUMBERS = f'"a": 1e400, "b": 1E5, "c": 0.12345678901234567890123, "d": {_BIG_INT}'

SCRUB_ROWS: list[tuple[str, str, str]] = [
    (
        '{"document_name": "Jane Doe CV.pdf", "source": "chat_query"}',
        '{"source": "chat_query"}',
        '{"source":"chat_query"}',
    ),
    ('{"document_name": null, "strategy": "simple"}', '{"strategy": "simple"}', '{"strategy":"simple"}'),
    ('{"source":"document_ingestion"}',) * 3,
    ('["document_name"]',) * 3,
    ("null",) * 3,
    (
        '{"document_name": "x.pdf", "note": "a\\u0000b", "z": 1, "a": 2}',
        '{"note": "a\\u0000b", "z": 1, "a": 2}',
        '{"note":"a\\u0000b","z":1,"a":2}',
    ),
    ('{"note":  "a\\u0000b"}',) * 3,
    (
        '{"document_name": "y.pdf", "note": "a\\udc80b", "z": 1}',
        '{"note": "a\\udc80b", "z": 1}',
        '{"note":"a\\udc80b","z":1}',
    ),
    ('{"document_name": "y.pdf", "note": "a\\ud800"}', '{"note": "a\\ud800"}', '{"note":"a\\ud800"}'),
    ('{"note": "a\\ud800",  "k":1}',) * 3,
    ('{"note": "document_name"}',) * 3,
    (
        '{"z": 1, "document_name": "y.pdf", "a": [1, {"c": 2}]}',
        '{"z": 1, "a": [1, {"c": 2}]}',
        '{"z":1,"a":[1,{"c":2}]}',
    ),
    ('{"document_name": "x.pdf"}', "{}", "{}"),
    (
        '{"document_name": "x", ' + _NUMBERS + "}",
        "{" + _NUMBERS + "}",
        "{" + _NUMBERS.replace(": ", ":").replace(", ", ",") + "}",
    ),
    (
        '{"document_name": "x", "v": "\\u4e2d", "w": "é"}',
        '{"v": "\\u4e2d", "w": "\\u00e9"}',
        '{"v":"\\u4e2d","w":"é"}',
    ),
    ('{"document_name": "x", "名": 1}', '{"\\u540d": 1}', '{"名":1}'),
    # json_remove drops only the first duplicate; the scrub must remove both.
    ('{"document_name":"x","k":1,"document_name":"y"}', '{"k": 1}', '{"k":1}'),
]

REWRITTEN = sum(1 for stored, postgres, _ in SCRUB_ROWS if stored != postgres)
