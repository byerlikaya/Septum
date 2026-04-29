from __future__ import annotations

"""
In-memory debug store for chat LLM interactions.

This module keeps a short-lived, process-local record of what was sent
to and returned from the cloud LLM for each chat session. It never
stores the anonymization map or any raw PII; only masked prompts /
answers plus the final already-displayed answer are retained.

The store is bounded to ``_MAX_RECORDS`` entries (LRU eviction) so a
long-running process cannot accumulate hundreds of MB of historical
debug data. Operators that want a longer audit trail should rely on
``septum-audit`` instead.
"""

from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock
from typing import Optional

# 200 chat turns × ~1 KB masked prompt+answer is ~200 KB max — enough
# for a developer's debug panel to scrub recent history while staying
# bounded.
_MAX_RECORDS = 200


@dataclass
class ChatDebugRecord:
    """Debug payload for a single chat turn."""

    session_id: str
    masked_prompt: str
    masked_answer: str
    final_answer: str


_records: "OrderedDict[str, ChatDebugRecord]" = OrderedDict()
_lock = Lock()


def set_chat_debug_record(
    session_id: str,
    masked_prompt: str,
    masked_answer: str,
    final_answer: str,
) -> None:
    """Store or update the debug record for a chat session.

    LRU semantics: the most-recently-written id moves to the end; when
    the store hits ``_MAX_RECORDS`` the oldest id is evicted.
    """
    record = ChatDebugRecord(
        session_id=session_id,
        masked_prompt=masked_prompt,
        masked_answer=masked_answer,
        final_answer=final_answer,
    )
    with _lock:
        if session_id in _records:
            _records.move_to_end(session_id)
        _records[session_id] = record
        while len(_records) > _MAX_RECORDS:
            _records.popitem(last=False)


def get_chat_debug_record(session_id: str) -> Optional[ChatDebugRecord]:
    """Return the debug record for a chat session, if available."""
    with _lock:
        record = _records.get(session_id)
        if record is not None:
            _records.move_to_end(session_id)
        return record
