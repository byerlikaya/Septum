from __future__ import annotations

"""Shared path-free error handling for encrypted search-index files."""

from pathlib import Path


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
