"""Duplicate spreadsheet schemas and columns shared by the dedupe tests.

Seeded into a database that has no unique index on the two tables yet (an
Alembic revision before 024, or an older SQLite file), then deduplicated by
revision 024 or by SQLite startup. Ids are explicit so the kept rows are
known in advance.
"""

from __future__ import annotations

import sqlalchemy as sa

DOCUMENTS = (1, 2, 3)

# (id, document_id); 2 and 5 duplicate document 1's schema 1.
SCHEMAS = ((1, 1), (2, 1), (3, 2), (4, 3), (5, 1))

# (id, schema_id, index, semantic_label, is_numeric)
COLUMNS = (
    (1, 1, 0, None, None),
    (2, 1, 1, "Kept1", None),
    (3, 2, 0, "FromDup", None),
    (4, 2, 2, None, None),
    (5, 3, 0, None, True),
    (6, 3, 0, "   ", None),
    (7, 3, 0, "Second", None),
    (8, 3, 0, "Third", True),
    (9, 3, 1, None, None),
    (10, 3, 1, None, False),
    (11, 4, 0, "Clean", None),
    (12, 5, 1, "Later", None),
)

# (document_id, schema_id, index, column id, semantic_label) after dedupe.
EXPECTED = [
    (1, 1, 0, 3, "FromDup"),
    (1, 1, 1, 2, "Kept1"),
    (1, 1, 2, 4, None),
    (2, 3, 0, 8, "Third"),
    (2, 3, 1, 10, None),
    (3, 4, 0, 11, "Clean"),
]

# Columns moved to the kept schema, schemas deleted, columns deleted.
EXPECTED_COUNTS = (3, 2, 6)
EXPECTED_LOG = (
    "moved {} columns, deleted {} duplicate schemas and {} duplicate columns; "
    "0 documents exceed the labelled column budget"
).format(*EXPECTED_COUNTS)

UNIQUE_INDEXES = {
    "uq_spreadsheet_schemas_document_id": (["document_id"], True),
    "uq_spreadsheet_columns_schema_id_index": (["schema_id", "index"], True),
}
SUPERSEDED_INDEXES = {
    "ix_spreadsheet_schemas_document_id": (["document_id"], False),
    "ix_spreadsheet_columns_schema_id": (["schema_id"], False),
}


def insert_schemas(conn: sa.Connection, rows: list[tuple[int, int]]) -> None:
    """Insert schema rows given as (id, document_id)."""
    conn.execute(
        sa.text(
            "INSERT INTO spreadsheet_schemas (id, document_id, created_at, updated_at) "
            "VALUES (:id, :doc, '2026-01-01', '2026-01-01')"
        ),
        [{"id": schema_id, "doc": doc_id} for schema_id, doc_id in rows],
    )


def insert_columns(conn: sa.Connection, rows: list[tuple]) -> None:
    """Insert column rows given as (id or None, schema_id, index, label, is_numeric)."""
    conn.execute(
        sa.text(
            'INSERT INTO spreadsheet_columns (id, schema_id, "index", technical_label, '
            "semantic_label, is_numeric) "
            "VALUES (:id, :schema, :index, :technical, :label, :numeric)"
        ),
        [
            {
                "id": column_id,
                "schema": schema_id,
                "index": index,
                "technical": f"COLUMN_{index + 1}",
                "label": label,
                "numeric": numeric,
            }
            for column_id, schema_id, index, label, numeric in rows
        ],
    )


def seed(conn: sa.Connection) -> None:
    """Insert the documents, duplicate schemas and duplicate columns."""
    conn.execute(
        sa.text(
            "INSERT INTO documents (id, filename, original_filename, file_type, "
            "file_format, detected_language, uploaded_at, encrypted_path, chunk_count, "
            "entity_count, ingestion_status, file_size_bytes, active_regulation_ids) "
            "VALUES (:id, 'a', 'a', 'text/csv', 'csv', 'en', '2026-01-01', 'p', 0, 0, "
            "'completed', 1, '[]')"
        ),
        [{"id": doc_id} for doc_id in DOCUMENTS],
    )
    insert_schemas(conn, list(SCHEMAS))
    insert_columns(conn, list(COLUMNS))


def spreadsheet_indexes(conn: sa.Connection) -> dict[str, tuple[list[str], bool]]:
    """Return every index of the two tables as name -> (columns, unique)."""
    inspector = sa.inspect(conn)
    return {
        i["name"]: (i["column_names"], bool(i["unique"]))
        for table in ("spreadsheet_schemas", "spreadsheet_columns")
        for i in inspector.get_indexes(table)
    }


def stored(conn: sa.Connection) -> list[tuple]:
    """Return every column with its schema's document, in the EXPECTED shape."""
    rows = conn.execute(
        sa.text(
            'SELECT s.document_id, s.id, c."index", c.id, c.semantic_label '
            "FROM spreadsheet_columns c JOIN spreadsheet_schemas s ON s.id = c.schema_id "
            'ORDER BY s.document_id, c."index", c.id'
        )
    )
    return [tuple(row) for row in rows]


def schema_count(conn: sa.Connection) -> int:
    """Return the number of stored schema rows."""
    return conn.execute(sa.text("SELECT COUNT(*) FROM spreadsheet_schemas")).scalar_one()
