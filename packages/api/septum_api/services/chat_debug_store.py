from __future__ import annotations

"""
In-memory debug store for chat LLM interactions.

This module keeps a short-lived, process-local record of what was sent to and
returned from the cloud LLM for each chat session. It never stores the
anonymization map or any raw PII; only masked prompts/answers plus the final
already-displayed answer are retained.
"""

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class ChatDebugRecord:
    """Debug payload for a single chat turn."""

    session_id: str
    masked_prompt: str
    masked_answer: str
    final_answer: str


_records: Dict[str, ChatDebugRecord] = {}


def set_chat_debug_record(
    session_id: str,
    masked_prompt: str,
    masked_answer: str,
    final_answer: str,
) -> None:
    """Store or update the debug record for a chat session."""
    _records[session_id] = ChatDebugRecord(
        session_id=session_id,
        masked_prompt=masked_prompt,
        masked_answer=masked_answer,
        final_answer=final_answer,
    )


def get_chat_debug_record(session_id: str) -> Optional[ChatDebugRecord]:
    """Return the debug record for a chat session, if available."""
    return _records.get(session_id)


