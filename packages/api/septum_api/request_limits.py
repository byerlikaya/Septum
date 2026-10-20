"""Size limits of API request payloads, shared by the routers that enforce them.

Every bound a request model or query parameter enforces with HTTP 422 lives
here, so related endpoints cannot drift apart (a chunk the approval modal
offers must fit the chunk edit and retry limits, for example).
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

TOP_K_RETRIEVAL_MAX_CAP = 25
"""Most chunks retrieval returns per document for one chat turn."""

MAX_CONTEXT_CHUNKS = 2 * TOP_K_RETRIEVAL_MAX_CAP
"""Most chunks one turn can put in front of the cloud LLM, and therefore the
most a ``pre_approved_chunks`` retry may send back."""

MAX_RETRY_DOCUMENTS = MAX_CONTEXT_CHUNKS
"""Most documents one chat turn may name; more could never all contribute."""

MAX_PRE_APPROVED_CHUNK_CHARS = 20_000
"""Longest chunk text a client may send (retry, approval edit, chunk edit)."""

MAX_CHAT_MESSAGE_CHARS = 20_000
"""Longest chat question."""

MAX_SESSION_ID_LENGTH = 128
"""Longest client-supplied chat session id."""

MAX_PROGRESS_IDS = 200
"""Most document ids one processing-progress request may ask about."""

MAX_DOCUMENT_ID = 2**31 - 1
"""Largest value ``documents.id`` (a 32-bit integer column) can hold."""

DocumentId = Annotated[int, Field(ge=1, le=MAX_DOCUMENT_ID)]
"""A client-supplied document id, refused with HTTP 422 outside the column's range."""

__all__ = [
    "DocumentId",
    "MAX_CHAT_MESSAGE_CHARS",
    "MAX_CONTEXT_CHUNKS",
    "MAX_DOCUMENT_ID",
    "MAX_PRE_APPROVED_CHUNK_CHARS",
    "MAX_PROGRESS_IDS",
    "MAX_RETRY_DOCUMENTS",
    "MAX_SESSION_ID_LENGTH",
    "TOP_K_RETRIEVAL_MAX_CAP",
]
