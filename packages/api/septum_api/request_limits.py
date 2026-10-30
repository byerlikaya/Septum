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

MIN_CHUNK_SIZE = 100
"""Smallest text chunk size setting: the chunkers' own minimum chunk length."""

MIN_PDF_CHUNK_SIZE = 800
"""Smallest PDF/DOCX chunk size setting: the structured chunker never uses less."""

MAX_CHUNK_OVERLAP = MAX_PRE_APPROVED_CHUNK_CHARS // 2
"""Largest chunk overlap setting; see :func:`max_chunk_overlap`."""


def max_chunk_overlap(chunk_size: int) -> int:
    """Return the largest overlap allowed with ``chunk_size``: half of it.

    The sliding window advances by ``chunk_size - chunk_overlap`` characters,
    so this keeps every step at least half a chunk long and the chunk count
    at most about twice the no-overlap count.
    """
    return chunk_size // 2


def chunk_overlap_fits(chunk_size: int, chunk_overlap: int) -> bool:
    """Return whether ``chunk_overlap`` is allowed with ``chunk_size``; see :func:`max_chunk_overlap`."""
    return chunk_overlap <= max_chunk_overlap(chunk_size)


MAX_CHAT_MESSAGE_CHARS = 20_000
"""Longest chat question."""

MAX_SESSION_ID_LENGTH = 128
"""Longest client-supplied chat session id."""

MAX_LLM_PROVIDER_LENGTH = 64
"""Longest LLM provider name a client may send (the known providers are one word each)."""

MAX_LLM_MODEL_LENGTH = 128
"""Longest LLM model id a client may send (provider-prefixed ids such as OpenRouter's stay well inside it)."""

MAX_PROGRESS_IDS = 200
"""Most document ids one processing-progress request may ask about."""

MAX_SHEET_COLUMNS = 16_384
"""Most columns a spreadsheet can hold (the xlsx ceiling, XFD)."""

MAX_SCHEMA_LABELED_COLUMNS = 1024
"""Most columns of one document's schema that may carry a semantic label.

A schema update that would leave more labelled columns is refused with HTTP
422 before the PII gate runs. A chat turn describes at most this many columns,
in index order, and gates only the labelled ones among them, so rows stored
before the bound cannot raise the per-turn NER cost or the prompt size either.
"""

MAX_SCHEMA_LABEL_CHARS = 128
"""Longest semantic column label an editor may type."""

MAX_TECHNICAL_LABEL_CHARS = 64
"""Longest technical column label a client may send."""

MAX_DOCUMENT_ID = 2**31 - 1
"""Largest value ``documents.id`` (a 32-bit integer column) can hold."""

DocumentId = Annotated[int, Field(ge=1, le=MAX_DOCUMENT_ID)]
"""A client-supplied document id, refused with HTTP 422 outside the column's range."""

__all__ = [
    "DocumentId",
    "MAX_CHAT_MESSAGE_CHARS",
    "MAX_CHUNK_OVERLAP",
    "MAX_CONTEXT_CHUNKS",
    "MAX_DOCUMENT_ID",
    "MAX_LLM_MODEL_LENGTH",
    "MAX_LLM_PROVIDER_LENGTH",
    "MAX_PRE_APPROVED_CHUNK_CHARS",
    "MAX_PROGRESS_IDS",
    "MAX_RETRY_DOCUMENTS",
    "MAX_SCHEMA_LABELED_COLUMNS",
    "MAX_SCHEMA_LABEL_CHARS",
    "MAX_SESSION_ID_LENGTH",
    "MAX_SHEET_COLUMNS",
    "MAX_TECHNICAL_LABEL_CHARS",
    "MIN_CHUNK_SIZE",
    "MIN_PDF_CHUNK_SIZE",
    "TOP_K_RETRIEVAL_MAX_CAP",
    "chunk_overlap_fits",
    "max_chunk_overlap",
]
