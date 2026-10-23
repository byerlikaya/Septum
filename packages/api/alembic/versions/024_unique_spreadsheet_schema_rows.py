"""Make spreadsheet schemas unique per document and columns unique per index.

Two concurrent first writes of a document's schema could store two schema
rows, and every later single-row lookup of that document's schema then
failed. Unique indexes ``uq_spreadsheet_schemas_document_id`` and
``uq_spreadsheet_columns_schema_id_index`` (schema_id, index) now refuse the
second row; they supersede the single-column ``ix_`` indexes on the same
leading column, which are dropped.

Existing duplicates are removed first, deterministically:

1. Per document the oldest schema (lowest id) is kept. The columns of every
   other schema of that document are moved to it, so no stored label is
   lost with its schema row.
2. The other schema rows, now without columns, are deleted.
3. Per (schema_id, index) one row is kept: one with a non-blank semantic
   label first, then one with ``is_numeric`` set, then the lowest id. The
   others are deleted; only there can a label be lost, when two rows of one
   slot both had one.

The counts are logged, with the number of documents whose merged schema now
holds more labelled columns than an update may leave (1024). Chat already
caps what it sends; an update of such a document is refused until enough
labels are cleared. On PostgreSQL both tables are locked against writes
for the rest of the migration transaction, so no duplicate is written
between the dedupe and the index build. Each index step checks the live
schema first, so the upgrade is a no-op on a database already in the target
shape. Downgrade restores the single-column indexes and drops the unique
ones; the merged and deleted rows are not restored.

Revision ID: 024
Revises: 023
"""

import logging

import sqlalchemy as sa
from alembic import op

revision = "024"
down_revision = "023"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

_SCHEMAS = "spreadsheet_schemas"
_COLUMNS = "spreadsheet_columns"
_SCHEMAS_UNIQUE = "uq_spreadsheet_schemas_document_id"
_COLUMNS_UNIQUE = "uq_spreadsheet_columns_schema_id_index"
_SCHEMAS_SINGLE = "ix_spreadsheet_schemas_document_id"
_COLUMNS_SINGLE = "ix_spreadsheet_columns_schema_id"

# Columns of a document's other schemas move to its oldest (lowest id) one;
# those schemas are deleted; one row per (schema_id, index) is kept.
_DEDUPE_STATEMENTS = (
    "UPDATE spreadsheet_columns SET schema_id = ("
    "SELECT MIN(keeper.id) FROM spreadsheet_schemas keeper "
    "JOIN spreadsheet_schemas own ON own.document_id = keeper.document_id "
    "WHERE own.id = spreadsheet_columns.schema_id) "
    "WHERE schema_id IN ("
    "SELECT dup.id FROM spreadsheet_schemas dup WHERE dup.id > ("
    "SELECT MIN(k.id) FROM spreadsheet_schemas k WHERE k.document_id = dup.document_id))",
    "DELETE FROM spreadsheet_schemas WHERE id > ("
    "SELECT MIN(k.id) FROM spreadsheet_schemas k "
    "WHERE k.document_id = spreadsheet_schemas.document_id)",
    "DELETE FROM spreadsheet_columns WHERE id IN ("
    "SELECT id FROM (SELECT id, ROW_NUMBER() OVER ("
    'PARTITION BY schema_id, "index" '
    "ORDER BY CASE WHEN TRIM(COALESCE(semantic_label, '')) <> '' THEN 0 ELSE 1 END, "
    "CASE WHEN is_numeric IS NOT NULL THEN 0 ELSE 1 END, id"
    ") AS position FROM spreadsheet_columns) ranked WHERE position > 1)",
)
# The labelled-column budget of a schema update when this revision was written.
_LABEL_BUDGET = 1024
_OVER_LABEL_BUDGET = (
    "SELECT COUNT(*) FROM (SELECT schema_id FROM spreadsheet_columns "
    "WHERE semantic_label IS NOT NULL AND semantic_label <> '' "
    "GROUP BY schema_id HAVING COUNT(*) > :budget) over_budget"
)


def _indexes(table: str) -> set[str]:
    return {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)}


def _dedupe(conn: sa.Connection) -> tuple[int, int, int, int]:
    """Remove duplicate schemas and columns.

    Returns the columns moved, schemas deleted, columns deleted, and the
    schemas left over the labelled-column budget (only counted when a row
    changed, else 0).
    """
    if conn.dialect.name == "postgresql":
        conn.execute(
            sa.text(f"LOCK TABLE {_SCHEMAS}, {_COLUMNS} IN SHARE ROW EXCLUSIVE MODE")
        )
    moved, schemas, columns = (
        conn.execute(sa.text(statement)).rowcount for statement in _DEDUPE_STATEMENTS
    )
    over_budget = 0
    if moved or schemas or columns:
        over_budget = conn.execute(
            sa.text(_OVER_LABEL_BUDGET), {"budget": _LABEL_BUDGET}
        ).scalar_one()
    return moved, schemas, columns, over_budget


def upgrade() -> None:
    moved, schemas, columns, over_budget = _dedupe(op.get_bind())
    if moved or schemas or columns:
        logger.warning(
            "Spreadsheet schema dedupe: moved %d columns, deleted %d duplicate "
            "schemas and %d duplicate columns; %d documents exceed the labelled "
            "column budget",
            moved,
            schemas,
            columns,
            over_budget,
        )
    for table, unique, columns_of_unique, single in (
        (_SCHEMAS, _SCHEMAS_UNIQUE, ["document_id"], _SCHEMAS_SINGLE),
        (_COLUMNS, _COLUMNS_UNIQUE, ["schema_id", "index"], _COLUMNS_SINGLE),
    ):
        existing = _indexes(table)
        if unique not in existing:
            op.create_index(unique, table, columns_of_unique, unique=True)
        if single in existing:
            op.drop_index(single, table_name=table)


def downgrade() -> None:
    """Restore the single-column indexes; the removed duplicates stay removed."""
    for table, unique, single, single_column in (
        (_SCHEMAS, _SCHEMAS_UNIQUE, _SCHEMAS_SINGLE, "document_id"),
        (_COLUMNS, _COLUMNS_UNIQUE, _COLUMNS_SINGLE, "schema_id"),
    ):
        existing = _indexes(table)
        if single not in existing:
            op.create_index(single, table, [single_column])
        if unique in existing:
            op.drop_index(unique, table_name=table)
