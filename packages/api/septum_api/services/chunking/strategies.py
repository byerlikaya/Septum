"""Chunker v2: the four strategies over a document's text blocks.

Pure: no I/O, no model, no database. The input is the ingester's blocks and
the document-level protected ranges (``septum_core.span_projection``); the
output is :class:`ChunkRecord` offsets into ``join_blocks(blocks)``.

Block ``i`` owns the region from its own start to the next block's start,
separator included, so every cut at a block boundary leaves the separator
with the chunk before it and the chunks leave no gap.

Sizes are characters; the hard character cap ``MAX_PRE_APPROVED_CHUNK_CHARS``
applies to every chunk.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from functools import lru_cache
from typing import Iterable, List, Literal, Optional, Sequence, Tuple, get_args

from ...request_limits import (
    MAX_PRE_APPROVED_CHUNK_CHARS,
    MAX_SECTION_TITLE_CHARS,
    MIN_CHUNK_SIZE,
    max_chunk_overlap,
)
from ..ingestion.base import TextBlock, join_blocks
from .cutting import Cutter
from .records import ChunkRecord

Strategy = Literal["structured", "sliding", "table", "audio"]

_STRATEGIES = get_args(Strategy)
_TITLE_KIND = {"structured": "heading", "table": "sheet_header"}


@lru_cache(maxsize=None)
def _separator_length(kind: str) -> int:
    """Length of the text ``join_blocks`` puts before a non-first block of ``kind``."""
    return len(join_blocks([TextBlock("", "text"), TextBlock("", kind)]))


class _Layout:
    """Document offsets of the blocks, and what they say about a position."""

    def __init__(self, blocks: Sequence[TextBlock], strategy: str, cutter: Cutter) -> None:
        self.blocks = blocks
        self._cutter = cutter
        self.starts: List[int] = []
        position = 0
        for index, block in enumerate(blocks):
            if index:
                position += _separator_length(block.kind)
            self.starts.append(position)
            position += len(block.text)
        kind = _TITLE_KIND.get(strategy)
        titles = [
            (start, block.text)
            for start, block in zip(self.starts, blocks)
            if kind and block.kind == kind
        ]
        self._title_starts = [start for start, _ in titles]
        self._titles = [title for _, title in titles]
        self._title_ends = [start + len(title) for start, title in titles]

    def block_at(self, offset: int) -> TextBlock:
        return self.blocks[max(0, bisect_right(self.starts, offset) - 1)]

    def title_at(self, offset: int) -> Optional[Tuple[str, int, int]]:
        """Title in effect at ``offset`` and the document offsets of its whole source.

        A title that begins strictly inside a protected range starts at that
        range's end instead (no title when none is left); one longer than
        ``MAX_SECTION_TITLE_CHARS`` is cut at the largest offset within the
        limit that is not strictly inside a protected range; when a range
        holds the limit and starts at the title's own start, the title is empty.
        """
        i = bisect_right(self._title_starts, offset) - 1
        if i < 0:
            return None
        full, source_start, source_end = self._titles[i], self._title_starts[i], self._title_ends[i]
        first = min(self._cutter.next_valid(source_start), source_end)
        if first >= source_end and source_end > source_start:
            return None
        title = full[first - source_start :]
        keep = min(len(title), MAX_SECTION_TITLE_CHARS)
        while keep > 0 and not self._cutter.is_valid(first + keep):
            keep -= 1
        return title[:keep], first, source_end

    def record(self, index: int, text: str, strategy: str, start: int, end: int) -> ChunkRecord:
        first = self.block_at(start)
        last = self.block_at(end - 1)
        title = self.title_at(start)
        return ChunkRecord(
            index=index,
            start=start,
            end=end,
            text=text[start:end],
            strategy=strategy,
            page=first.page,
            sheet=first.sheet,
            t_start=first.t_start,
            t_end=last.t_end,
            section_title=title[0] if title else None,
            section_title_start=title[1] if title else None,
            section_title_end=title[2] if title else None,
        )

    def row_starts(self) -> List[int]:
        """Document offset of every row of every ``rows`` block."""
        out: List[int] = []
        for start, block in zip(self.starts, self.blocks):
            if block.kind == "rows" and block.row_offsets is not None:
                out.extend(start + int(offset) for offset in block.row_offsets)
        return out


def _table_group_starts(layout: _Layout, cells: int) -> List[int]:
    """Offsets where a table chunk may start: each block, and every ``cells`` cells inside rows."""
    starts: List[int] = []
    previous: Optional[TextBlock] = None
    for start, block in zip(layout.starts, layout.blocks):
        continues_header = (
            block.kind == "rows"
            and previous is not None
            and previous.kind == "sheet_header"
            and previous.sheet == block.sheet
        )
        if not continues_header:
            starts.append(start)
        offsets = block.row_offsets if block.kind == "rows" else None
        if offsets is not None and len(offsets):
            columns = block.text.split("\n", 1)[0].count("\t") + 1
            per_chunk = max(1, cells // columns)
            starts.extend(start + int(offsets[k]) for k in range(per_chunk, len(offsets), per_chunk))
        previous = block
    return starts


def _audio_group_starts(layout: _Layout, seconds: float) -> List[int]:
    """Offsets of the segment boundaries that open a new audio chunk."""
    starts: List[int] = []
    origin: Optional[float] = None
    for start, block in zip(layout.starts, layout.blocks):
        if block.kind != "segment" or block.t_start is None or block.t_end is None:
            starts.append(start)
            origin = None
        elif origin is None or block.t_end - origin > seconds:
            starts.append(start)
            origin = block.t_start
    return starts


def chunk_blocks(
    blocks: Sequence[TextBlock],
    strategy: Strategy,
    *,
    protected: Iterable[Tuple[int, int]] = (),
    chunk_size: int = 800,
    overlap: int = 200,
    spreadsheet_chunk_size: int = 200,
    audio_chunk_size: float = 60.0,
) -> List[ChunkRecord]:
    """Chunk ``blocks`` with ``strategy``; deterministic for equal input.

    ``structured`` packs whole blocks up to ``chunk_size`` and splits an
    oversized block with overlap. ``sliding`` is a window of ``chunk_size``
    with ``overlap`` over the whole text. ``table`` puts ``spreadsheet_chunk_size``
    cells in a chunk, cut on row boundaries, with the sheet header as the
    section title. ``audio`` groups Whisper segments into ``audio_chunk_size``
    seconds; groups start only at segment boundaries, and a group longer than
    the ceiling is still cut inside a segment, on a text boundary. ``chunk_size`` is clamped to
    ``[MIN_CHUNK_SIZE, MAX_PRE_APPROVED_CHUNK_CHARS]`` and ``overlap`` to
    ``[0, chunk_size // 2]`` characters. ``protected`` are document-level ``(start, end)``
    ranges a cut never falls strictly inside; see ``cutting`` for the one
    case where a chunk outgrows its budget because of one. A chunk is
    whitespace-only only when a block is.
    """
    if strategy not in _STRATEGIES:
        raise ValueError(f"unknown chunking strategy: {strategy!r}")
    text = join_blocks(blocks)
    cutter = Cutter(text, protected, MAX_PRE_APPROVED_CHUNK_CHARS)
    if not text:
        return []
    layout = _Layout(blocks, strategy, cutter)

    if strategy in ("structured", "sliding"):
        budget = min(max(chunk_size, MIN_CHUNK_SIZE), MAX_PRE_APPROVED_CHUNK_CHARS)
        window = min(
            max(overlap, 0),
            max_chunk_overlap(min(max(chunk_size, MIN_CHUNK_SIZE), MAX_PRE_APPROVED_CHUNK_CHARS)),
        )
        anchors = layout.starts[1:] if strategy == "structured" else []
        spans = cutter.pack(0, len(text), anchors, budget, window)
    else:
        if strategy == "table":
            groups = _table_group_starts(layout, max(1, spreadsheet_chunk_size))
            inner = sorted(set(layout.starts + layout.row_starts()))
        else:
            groups = _audio_group_starts(layout, audio_chunk_size)
            inner = sorted(set(layout.starts))
        edges = sorted({g for g in groups if g > 0 and cutter.is_valid(g)} | {0, len(text)})
        spans = []
        for lo, hi in zip(edges, edges[1:]):
            usable = inner[bisect_right(inner, lo) : bisect_left(inner, hi)]
            spans.extend(cutter.pack(lo, hi, usable, MAX_PRE_APPROVED_CHUNK_CHARS, 0))

    return [
        layout.record(index, text, strategy, start, end)
        for index, (start, end) in enumerate(spans)
    ]
