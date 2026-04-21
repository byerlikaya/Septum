"""In-memory document processing progress tracker.

Stores per-document progress percentages during background ingestion.
Values are ephemeral — they only exist while the document is being processed.
"""

_progress: dict[int, int] = {}


def set_progress(doc_id: int, percent: int) -> None:
    _progress[doc_id] = min(percent, 99)


def get_progress(doc_id: int) -> int:
    return _progress.get(doc_id, 0)


def clear_progress(doc_id: int) -> None:
    _progress.pop(doc_id, None)
