from __future__ import annotations

"""Shared, path-free file handling for encrypted search-index files.

Index files are written atomically (a temp file in the same directory,
flushed and fsynced, then renamed over the target), so a reader never sees
half of a file and a crash mid-write leaves the previous index in place.
Readers never delete a file they cannot read.
"""

import logging
import os
import time
from pathlib import Path
from typing import Optional
from uuid import uuid4

from ..request_limits import MAX_DOCUMENT_ID

logger = logging.getLogger(__name__)

# A temp file older than this is a leftover of a crashed save; a younger one
# may be a save still in flight in another worker.
STALE_TEMP_SECONDS = 300
_TEMP_SUFFIX = ".tmp"


def index_io_error(label: str, document_id: int, action: str, exc: OSError) -> RuntimeError:
    """Path-free error for a failed ``label`` index file operation (e.g. FAISS, BM25)."""
    return RuntimeError(
        f"Could not {action} {label} index for document {document_id} ({type(exc).__name__})"
    )


def write_index_atomically(path: Path, data: bytes, label: str, document_id: int) -> None:
    """Replace ``path`` with ``data`` in one rename; failures raise without the path.

    The temp file is created exclusively with owner-only permissions next to
    the target, fsynced before the rename, and removed again on failure.
    """
    tmp = path.with_name(f"{path.name}.{uuid4().hex}{_TEMP_SUFFIX}")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        _remove_quietly(tmp)
        raise index_io_error(label, document_id, "write", exc) from None
    except BaseException:
        _remove_quietly(tmp)
        raise


def _remove_quietly(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def sweep_stale_index_temp_files(directory: Path) -> int:
    """Remove temp files a crashed save left in ``directory``; return how many.

    Only files older than :data:`STALE_TEMP_SECONDS` go, so a save still in
    flight in another worker is never disturbed. Failures are logged by
    error class, without the path.
    """
    try:
        temps = [entry for entry in directory.iterdir() if entry.name.endswith(_TEMP_SUFFIX)]
    except FileNotFoundError:
        return 0
    cutoff = time.time() - STALE_TEMP_SECONDS
    removed = 0
    for temp in temps:
        try:
            if temp.stat().st_mtime < cutoff:
                temp.unlink(missing_ok=True)
                removed += 1
        except OSError as exc:
            logger.error("Could not remove a stale index temp file: %s", type(exc).__name__)
    if removed:
        logger.info("Removed %d stale index temp files", removed)
    return removed


def parse_document_id(text: str) -> Optional[int]:
    """Return ``text`` as a document id, or ``None`` if it cannot be one.

    Only plain decimal digits without a leading zero, within the
    ``documents.id`` column's range, count; a stray name, a zero-padded or an
    oversized suffix is ignored, never parsed.
    """
    if (
        not (text.isascii() and text.isdigit())
        or text.startswith("0")
        or len(text) > len(str(MAX_DOCUMENT_ID))
    ):
        return None
    value = int(text)
    return value if 1 <= value <= MAX_DOCUMENT_ID else None


def document_ids_in(directory: Path, prefix: str, suffix: str) -> set[int]:
    """Return the document ids of files named ``{prefix}{id}{suffix}`` in ``directory``.

    A missing directory holds no files; any other listing error propagates.
    """
    try:
        names = [entry.name for entry in directory.iterdir()]
    except FileNotFoundError:
        return set()
    ids = set()
    for name in names:
        if len(name) > len(prefix) + len(suffix) and name.startswith(prefix) and name.endswith(suffix):
            document_id = parse_document_id(name[len(prefix):len(name) - len(suffix)])
            if document_id is not None:
                ids.add(document_id)
    return ids
