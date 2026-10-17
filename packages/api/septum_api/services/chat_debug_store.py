from __future__ import annotations

"""
In-memory debug store for chat LLM interactions.

This module keeps a short-lived, process-local record of what was sent
to and returned from the cloud LLM for each chat turn. Only masked
content is retained — the masked prompt and the masked answer exactly
as they crossed the cloud boundary. The de-anonymized answer, the
anonymization map and any raw text are never stored here.

Records are keyed by a server-generated, unguessable ``debug_id`` and
remember the id of the user whose turn produced them, so a client-chosen
session id can neither collide with nor read another user's record.

The store is bounded to ``_MAX_RECORDS`` entries (LRU eviction) so a
long-running process cannot accumulate hundreds of MB of historical
debug data. Operators that want a longer audit trail should rely on
``septum-audit`` instead.
"""

import secrets
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock
from typing import Optional

# 200 chat turns × ~1 KB masked prompt+answer is ~200 KB max — enough
# for a developer's debug panel to scrub recent history while staying
# bounded.
_MAX_RECORDS = 200


@dataclass(frozen=True)
class ChatDebugRecord:
    """Masked debug payload for a single chat turn."""

    debug_id: str
    owner_user_id: Optional[int]
    masked_prompt: str
    masked_answer: str


_records: "OrderedDict[str, ChatDebugRecord]" = OrderedDict()
_lock = Lock()


def new_debug_id() -> str:
    """Return a fresh unguessable identifier for a debug record."""
    return secrets.token_urlsafe(24)


def set_chat_debug_record(
    debug_id: str,
    owner_user_id: Optional[int],
    masked_prompt: str,
    masked_answer: str,
) -> None:
    """Store the masked debug record for one chat turn.

    LRU semantics: the most-recently-written id moves to the end; when
    the store hits ``_MAX_RECORDS`` the oldest id is evicted.
    """
    record = ChatDebugRecord(
        debug_id=debug_id,
        owner_user_id=owner_user_id,
        masked_prompt=masked_prompt,
        masked_answer=masked_answer,
    )
    with _lock:
        if debug_id in _records:
            _records.move_to_end(debug_id)
        _records[debug_id] = record
        while len(_records) > _MAX_RECORDS:
            _records.popitem(last=False)


def get_chat_debug_record(
    debug_id: str,
    requester_user_id: int,
    requester_is_admin: bool,
) -> Optional[ChatDebugRecord]:
    """Return the debug record if it exists and the requester may read it.

    Admins may read any record; every other user only the records their
    own turns produced. A foreign record is reported as absent so callers
    cannot distinguish "not yours" from "does not exist".
    """
    with _lock:
        record = _records.get(debug_id)
        if record is None:
            return None
        if not requester_is_admin and record.owner_user_id != requester_user_id:
            return None
        _records.move_to_end(debug_id)
        return record
