"""One-shot script: populate the entity index and document relationship
cache from the encrypted anon_maps already on disk.

Use after upgrading from a Septum version that pre-dates the entity
index tables, or any time those tables are out of sync with the
documents already ingested. The script never re-runs detection — it
only reads each document's persisted ``anon_maps/{id}.enc`` payload,
hashes the originals under the local encryption key, and writes the
index + relationship rows. Documents without a stored anon_map are
skipped with a warning.

Run from the repository root:

    python packages/api/scripts/rebuild_entity_index.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PACKAGE_DIR = SCRIPT_DIR.parent
os.chdir(PACKAGE_DIR)
sys.path.insert(0, str(PACKAGE_DIR))

from sqlalchemy import select  # noqa: E402

from septum_api import bootstrap  # noqa: E402
from septum_api.database import (  # noqa: E402
    build_database_url,
    get_session_maker,
    initialize_engine,
    init_db,
)
from septum_api.models.document import Document  # noqa: E402
from septum_api.services.document_anon_store import get_document_map  # noqa: E402
from septum_api.services.entity_index_service import (  # noqa: E402
    recompute_relationships_for_document,
    replace_index_for_document,
)


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("rebuild_entity_index")


async def main() -> int:
    config = bootstrap.get_config()
    if bootstrap.needs_setup():
        logger.error("Bootstrap not complete — run the setup wizard first")
        return 1
    initialize_engine(build_database_url(config.database_url, config.db_path))
    await init_db()

    sm = get_session_maker()
    indexed = 0
    skipped = 0
    rel_pairs = 0

    async with sm() as db:
        rows = await db.execute(select(Document.id, Document.original_filename))
        documents = list(rows.all())
        logger.info("found %d ingested documents", len(documents))

        for doc_id, filename in documents:
            anon_map = await get_document_map(doc_id)
            if anon_map is None or not anon_map.entity_map:
                logger.warning("doc_id=%s (%s) has no anon_map — skipping", doc_id, filename)
                skipped += 1
                continue
            written = await replace_index_for_document(db, doc_id, anon_map)
            await db.commit()
            indexed += 1
            logger.info(
                "doc_id=%s (%s) indexed %d entity rows", doc_id, filename, written
            )

        # Pair recomputation done in a second pass so each doc's neighbours
        # are already in the index by the time we score them.
        for doc_id, filename in documents:
            written = await recompute_relationships_for_document(db, doc_id)
            await db.commit()
            rel_pairs += written
            logger.info(
                "doc_id=%s (%s) wrote %d relationship pair rows",
                doc_id,
                filename,
                written,
            )

    logger.info(
        "done — indexed=%d skipped=%d total_relationship_rows=%d",
        indexed,
        skipped,
        rel_pairs,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
