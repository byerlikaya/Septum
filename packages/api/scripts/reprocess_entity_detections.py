"""One-shot script: rebuild every completed document with the current pipeline.

Each document is ingested again from its encrypted source file and run
through the same path as the reprocess endpoint: PII detection once over the
whole document, chunking, projection of the masked spans onto the chunks,
detection rows, the anonymization map and the FAISS / BM25 indexes. Rows,
counts and placeholders are therefore exactly what a fresh upload produces,
and the document's policy snapshot is refreshed, so it no longer reports as
outdated.

Run it with the API stopped, from ``packages/api/``. Each document is
claimed like the endpoint claims it, and its run fences every write on the
run token that claim returned, so a run an API process still had in flight
cannot write over it. It reads the same
configuration as the server (``config.json`` and environment overrides).
Only document ids, statuses and counts are printed; never file names or
document text.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PACKAGE_DIR = SCRIPT_DIR.parent
os.chdir(PACKAGE_DIR)
sys.path.insert(0, str(PACKAGE_DIR))


async def _reprocess_all() -> int:
    """Rebuild every completed document; return the number that failed."""
    from septum_api import bootstrap
    from septum_api.database import (
        build_database_url,
        get_session_maker,
        init_db,
        initialize_engine,
    )
    from septum_api.models.document import Document
    from septum_api.routers.documents import (
        claim_for_reingest,
        record_background_failure,
        reingest_document,
    )
    from sqlalchemy import select

    if bootstrap.needs_setup():
        raise SystemExit("Septum is not set up yet; run the setup wizard first.")
    config = bootstrap.get_config()
    initialize_engine(build_database_url(config.database_url, config.db_path))
    await init_db()

    maker = get_session_maker()
    async with maker() as db:
        ids = (
            await db.execute(
                select(Document.id)
                .where(Document.ingestion_status == "completed")
                .order_by(Document.id)
            )
        ).scalars().all()

    print(f"{'ID':<6} {'OLD':<6} {'NEW':<6} STATUS")
    failed = 0
    for document_id in ids:
        async with maker() as db:
            document = await db.get(Document, document_id)
            if document is None:
                continue
            old_count = str(document.entity_count)
            user_id = document.user_id
            run_token = await claim_for_reingest(db, document_id, only_completed=True)
            if run_token is None:
                print(f"{document_id:<6} {old_count:<6} {'-':<6} skipped")
                continue
            await db.refresh(document)
            try:
                await reingest_document(db, document, run_token=run_token)
            except Exception as exc:
                await record_background_failure(
                    db, document_id, exc, user_id, run_token=run_token
                )
                failed += 1
                print(f"{document_id:<6} {old_count:<6} {'-':<6} failed ({type(exc).__name__})")
                continue
            print(f"{document_id:<6} {old_count:<6} {str(document.entity_count):<6} {document.ingestion_status}")
    return failed


def main() -> None:
    failed = asyncio.run(_reprocess_all())
    if failed:
        raise SystemExit(f"{failed} document(s) failed; see the document list for the reason.")
    print("Done.")


if __name__ == "__main__":
    main()
