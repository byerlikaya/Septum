#!/usr/bin/env python3
"""Migrate data from an existing SQLite database to PostgreSQL.

Usage:
    DATABASE_URL=postgresql+asyncpg://septum:pass@localhost:5432/septum \
    python scripts/migrate_sqlite_to_pg.py [path/to/septum.db]

The PostgreSQL schema must already exist (run ``alembic upgrade head`` first).
This script copies row data only; it does NOT copy file artifacts (encrypted
documents, FAISS indexes, anonymization maps).
"""

from __future__ import annotations

import asyncio
import os
import sys

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

TABLES_IN_ORDER = [
    "app_settings",
    "regulation_rulesets",
    "custom_recognizers",
    "non_pii_rules",
    "text_normalization_rules",
    "documents",
    "chunks",
    "spreadsheet_schemas",
    "spreadsheet_columns",
    "errorlog",
]


async def migrate(sqlite_path: str, pg_url: str) -> None:
    """Read all rows from *sqlite_path* and insert into *pg_url*."""
    sqlite_url = f"sqlite+aiosqlite:///{sqlite_path}"
    src_engine = create_async_engine(sqlite_url, echo=False)
    dst_engine = create_async_engine(pg_url, echo=False)

    src_session_maker = async_sessionmaker(bind=src_engine, class_=AsyncSession)
    dst_session_maker = async_sessionmaker(bind=dst_engine, class_=AsyncSession)

    async with src_session_maker() as src, dst_session_maker() as dst:
        for table_name in TABLES_IN_ORDER:
            result = await src.execute(sa.text(f"SELECT * FROM {table_name}"))  # noqa: S608
            rows = result.mappings().all()
            if not rows:
                print(f"  {table_name}: 0 rows (skipped)")
                continue

            columns = list(rows[0].keys())
            col_list = ", ".join(columns)
            param_list = ", ".join(f":{c}" for c in columns)
            insert_sql = sa.text(
                f"INSERT INTO {table_name} ({col_list}) VALUES ({param_list}) "
                f"ON CONFLICT DO NOTHING"
            )

            for row in rows:
                await dst.execute(insert_sql, dict(row))

            print(f"  {table_name}: {len(rows)} rows migrated")

        await dst.commit()

    await src_engine.dispose()
    await dst_engine.dispose()
    print("\nMigration complete.")


def main() -> None:
    sqlite_path = sys.argv[1] if len(sys.argv) > 1 else "./septum.db"
    pg_url = os.getenv("DATABASE_URL")

    if not pg_url:
        print("ERROR: Set DATABASE_URL environment variable to the PostgreSQL connection string.")
        sys.exit(1)

    if pg_url.startswith("postgresql://"):
        pg_url = pg_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    if not os.path.exists(sqlite_path):
        print(f"ERROR: SQLite database not found at {sqlite_path}")
        sys.exit(1)

    print(f"Migrating data from {sqlite_path} to PostgreSQL...")
    asyncio.run(migrate(sqlite_path, pg_url))


if __name__ == "__main__":
    main()
