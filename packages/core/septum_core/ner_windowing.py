"""Token-window planning and cross-window merge for transformer NER.

A token-classification model sees at most its token budget, and the pipeline
truncates anything longer, silently. :func:`plan_windows` cuts a document into
overlapping windows of whole tokens; :func:`merge_window_spans` joins what the
windows found back into document coordinates.

A window reports a span whole when the span lies inside it without touching a
cut edge; the edge is a cut only where the window does not reach the text
boundary. Whole spans from every window are kept (a span seen by any window
is a real detection), and overlapping same-label spans are joined into one. A
span cut by an edge is kept only when no whole span of its label contains it,
so an entity longer than the overlap survives as the union of its fragments.
Two same-label spans separated only by spaces are joined when that gap lies
inside an overlap zone, never across a line break. Over-masking is preferred
to leaking a name.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

# Tokens shared by two neighbouring windows. An entity this long is cut in
# one window and seen whole in the next; a longer one is cut in both. Capped
# at a third of the window: the edge check is strict, so the overlap must be
# longer than the entity, and a third still leaves room to advance.
DEFAULT_OVERLAP_TOKENS = 64

# Characters allowed between two same-label spans that are joined across a
# seam: intra-line spaces only (ASCII space, no-break space, ideographic space).
# A tab, a line or paragraph separator, or any other character keeps the spans
# apart, because spreadsheet cells and list items are split that way.
_INTRA_LINE_SPACE = frozenset("  　")


class NERWindowingError(RuntimeError):
    """Windows cannot be planned or fitted to the model's token budget.

    The one exception the windowing code raises for a document it cannot
    cover. Invalid arguments to :func:`plan_windows` raise ``ValueError``.
    """


@dataclass(frozen=True)
class Window:
    """One window: its character extent in the document.

    ``first`` and ``last`` mark the text edges, where nothing is cut.
    """

    char_start: int
    char_end: int
    first: bool
    last: bool


@dataclass(frozen=True)
class Span:
    """A character span with its entity label, in document coordinates."""

    start: int
    end: int
    label: str
    score: float


def plan_windows(
    token_offsets: Sequence[tuple[int, int]],
    text_length: int,
    max_tokens: int,
    overlap_tokens: int | None = None,
) -> list[Window]:
    """Cut tokens into windows of at most ``max_tokens`` with ``overlap_tokens`` shared.

    ``token_offsets`` are the character offsets of the document's tokens in
    order. A document that fits in one window yields a single window covering
    all of it. A cut is never placed between two tokens that share characters
    (for example the pieces of one compatibility character), because
    re-tokenizing such a window would add tokens the plan did not count.
    Raises ``ValueError`` for an overlap that cannot leave room for progress
    (``overlap_tokens`` must be at most ``(max_tokens - 1) // 2``), and
    :class:`NERWindowingError` when no cut fits inside ``max_tokens``.
    """
    if overlap_tokens is None:
        overlap_tokens = min(DEFAULT_OVERLAP_TOKENS, max_tokens // 3)
    if not 0 <= overlap_tokens <= (max_tokens - 1) // 2:
        raise ValueError("overlap_tokens must be non-negative and at most (max_tokens - 1) // 2")
    if len(token_offsets) <= max_tokens:
        return [Window(0, text_length, True, True)]
    ranges = _token_ranges(token_offsets, max_tokens, overlap_tokens)
    last_index = len(ranges) - 1
    return [
        Window(
            char_start=0 if k == 0 else token_offsets[t0][0],
            char_end=text_length if k == last_index else token_offsets[t1 - 1][1],
            first=k == 0,
            last=k == last_index,
        )
        for k, (t0, t1) in enumerate(ranges)
    ]


def _cut_ok(token_offsets: Sequence[tuple[int, int]], index: int) -> bool:
    """Whether a cut may sit between token ``index - 1`` and token ``index``.

    Callers pass ``0 < index < len(token_offsets)``. Tokens that share
    characters with their predecessor must stay together.
    """
    return token_offsets[index][0] >= token_offsets[index - 1][1]


def _token_ranges(
    token_offsets: Sequence[tuple[int, int]],
    max_tokens: int,
    overlap: int,
) -> list[tuple[int, int]]:
    """Token index ranges ``[t0, t1)`` of the windows, every cut at a safe boundary."""
    count = len(token_offsets)
    ranges: list[tuple[int, int]] = []
    t0 = 0
    while True:
        t1 = min(t0 + max_tokens, count)
        if t1 < count:
            while t1 > t0 + 1 and not _cut_ok(token_offsets, t1):
                t1 -= 1
            if not _cut_ok(token_offsets, t1):
                raise NERWindowingError("no token boundary fits inside the window limit")
        ranges.append((t0, t1))
        if t1 >= count:
            return ranges
        nxt = max(t1 - overlap, t0 + 1)
        while nxt < t1 and not _cut_ok(token_offsets, nxt):
            nxt += 1
        t0 = t1 if nxt >= t1 else nxt


def _complete(span: Span, window: Window) -> bool:
    """Whether ``span`` lies inside ``window`` without touching a cut edge."""
    left_ok = window.first or span.start > window.char_start
    right_ok = window.last or span.end < window.char_end
    return left_ok and right_ok


def merge_window_spans(
    windows: Sequence[Window],
    per_window: Sequence[Iterable[Span]],
    text: str,
) -> list[Span]:
    """Union the spans of every window, each entity once; see the module docstring."""
    if len(windows) == 1:
        return list(per_window[0])
    lists = [list(spans) for spans in per_window]
    whole = [
        span
        for k, spans in enumerate(lists)
        for span in spans
        if _complete(span, windows[k])
    ]
    whole_index = _whole_index(whole)
    fragments = [
        span
        for k, spans in enumerate(lists)
        for span in spans
        if not _complete(span, windows[k]) and not _contained(span, whole_index)
    ]
    kept = sorted(set(whole + fragments), key=lambda s: (s.start, s.end, s.label))
    seams = [(windows[k + 1].char_start, windows[k].char_end) for k in range(len(windows) - 1)]
    return _join(kept, text, _SeamIndex(seams))


class _SeamIndex:
    """Overlap zones ``[start, end]``, one per cut, sorted by position.

    Starts and ends both increase along the text, so the zone that could
    contain a gap is the last one starting at or before the gap.
    """

    def __init__(self, seams: Sequence[tuple[int, int]]) -> None:
        self._starts = [start for start, _ in seams]
        self._ends = [end for _, end in seams]

    def bridges(self, prev_end: int, next_start: int) -> bool:
        """Whether the gap ``[prev_end, next_start)`` lies inside an overlap zone."""
        position = bisect_right(self._starts, prev_end) - 1
        return position >= 0 and next_start <= self._ends[position]


def _whole_index(whole: Sequence[Span]) -> dict[str, tuple[list[int], list[int]]]:
    """Per label: sorted start offsets and the running maximum end at each of them."""
    by_label: dict[str, list[Span]] = {}
    for span in whole:
        by_label.setdefault(span.label, []).append(span)
    index: dict[str, tuple[list[int], list[int]]] = {}
    for label, spans in by_label.items():
        spans.sort(key=lambda s: s.start)
        starts = [s.start for s in spans]
        running: list[int] = []
        best = -1
        for span in spans:
            best = max(best, span.end)
            running.append(best)
        index[label] = (starts, running)
    return index


def _contained(span: Span, index: dict[str, tuple[list[int], list[int]]]) -> bool:
    """Whether a whole span of the same label covers ``span``."""
    entry = index.get(span.label)
    if entry is None:
        return False
    starts, running = entry
    position = bisect_right(starts, span.start) - 1
    return position >= 0 and running[position] >= span.end


def _gap_joins(text: str, prev_end: int, next_start: int, seams: _SeamIndex) -> bool:
    if not seams.bridges(prev_end, next_start):
        return False
    return all(char in _INTRA_LINE_SPACE for char in text[prev_end:next_start])


def _join(spans: list[Span], text: str, seams: _SeamIndex) -> list[Span]:
    joined: list[Span] = []
    for span in spans:
        if joined:
            prev = joined[-1]
            if prev.label == span.label and (
                span.start < prev.end or _gap_joins(text, prev.end, span.start, seams)
            ):
                joined[-1] = Span(
                    start=prev.start,
                    end=max(prev.end, span.end),
                    label=prev.label,
                    score=max(prev.score, span.score),
                )
                continue
        joined.append(span)
    return joined
