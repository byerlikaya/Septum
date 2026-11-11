"""The record the chunker returns."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True, slots=True)
class ChunkRecord:
    """One chunk of a document, addressed by its position in the joined text.

    ``text`` is always ``joined[start:end]``. Chunks of one document are in
    order, ``index`` counts from zero, and together they cover the whole
    text: every chunk after the first starts at or before the previous end.
    ``page``, ``sheet`` and ``t_start`` come from the block holding ``start``;
    ``t_end`` from the block holding the last character. ``section_title`` is
    the heading (structured) or sheet header (table) in effect at ``start``,
    truncated to ``MAX_SECTION_TITLE_CHARS`` at an offset that is never
    strictly inside a protected range (empty if a range holding the limit
    starts at the title's start).

    A heading that starts strictly inside a protected range begins at that
    range's end: ``section_title_start`` is where ``section_title`` begins
    (``None`` title if no heading text is left after the range).
    ``section_title_start``/``section_title_end`` are DOCUMENT-level offsets of
    the untruncated source text from that start. They usually lie before ``start``,
    outside the chunk, so the caller must project the title from the
    document-level ranges, not the chunk's. Recommended path: project the full
    source ``[section_title_start, section_title_end)``, then truncate the
    masked text.

    ``text`` and ``section_title`` are raw document text: project or mask them
    before they leave the air-gapped zone. ``repr`` omits ``text``,
    ``section_title`` and ``sheet`` so raw text cannot reach a log.
    """

    index: int
    start: int
    end: int
    text: str = field(repr=False)
    strategy: str
    page: Optional[int] = None
    sheet: Optional[str] = field(default=None, repr=False)
    t_start: Optional[float] = None
    t_end: Optional[float] = None
    section_title: Optional[str] = field(default=None, repr=False)
    section_title_start: Optional[int] = None
    section_title_end: Optional[int] = None
