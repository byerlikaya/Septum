from __future__ import annotations

"""Shared path-free error handling for encrypted search-index files."""

from pathlib import Path
from typing import Optional

from ..request_limits import MAX_DOCUMENT_ID


def index_io_error(label: str, document_id: int, action: str, exc: OSError) -> RuntimeError:
    """Path-free error for a failed ``label`` index file operation (e.g. FAISS, BM25)."""
    return RuntimeError(
        f"Could not {action} {label} index for document {document_id} ({type(exc).__name__})"
    )


def discard_index_file(path: Path, label: str, document_id: int) -> None:
    """Remove an unreadable index file; failures raise without the path."""
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        raise index_io_error(label, document_id, "discard", exc) from None


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
