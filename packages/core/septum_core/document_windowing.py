"""Overlapping character windows for detection on very large documents.

Whole-document detection is exact but its time and memory grow with the text
(1,000,000 characters measured at 45 s and 3.3 GB). Above
``WindowingPolicy.max_whole_chars`` the detector runs every layer on
overlapping windows and merges what they found into full-text coordinates.
The model layers (spaCy, NER, semantic) must stay windowed: their memory grows
with the input and spaCy refuses text past its length limit. The pattern
recognizers hold no model state, and their matches have no length bound (a
custom regex can span any distance up to a closing delimiter), so they also
run once on the whole text and fill in what no window saw whole.
Overlap resolution and the non-PII filter then run once on the merged spans.

:func:`plan_text_windows` cuts the text and :func:`merge_window_spans` joins the
per-window spans. Both are pure; neither logs, stores or serializes text.

A window reports a span whole when the span lies inside it without touching a
cut edge. A span touching a cut edge may be a fragment, so it is dropped only
when a span that is whole in some window contains it: an entity shorter than
the overlap is seen whole by a neighbouring window and its fragments vanish,
while a longer one survives as the union of its fragments, even when a smaller
whole span (an e-mail address inside it) overlaps part of it, and a pattern
match is also reported whole by the full-text pass. Over-masking is preferred
to leaking.
"""

from __future__ import annotations

import unicodedata
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass

from .spans import DetectedSpan

# Longest text detected in one pass. Measured: 1,000,000 characters take 45 s
# and 3.3 GB with the Presidio layer; the spaCy limit is 2,000,000.
DEFAULT_MAX_WHOLE_CHARS = 1_000_000

# Window length above the threshold. Half the threshold keeps every window
# well inside the measured whole-document budget.
DEFAULT_WINDOW_CHARS = 500_000

# Characters two neighbouring windows share. A model-layer entity shorter
# than this is whole in one of them. Model entities (names, places,
# organisations, the semantic port's spans) are short phrases, so 10,000 is
# a wide margin at 2% of a window. Pattern matches do not depend on it: the
# full-text pattern pass sees them whole at any length.
DEFAULT_OVERLAP_CHARS = 10_000

_MIN_WINDOW_TO_OVERLAP = 4


@dataclass(frozen=True)
class WindowingPolicy:
    """Threshold, window length and overlap, in characters of the normalized text."""

    max_whole_chars: int = DEFAULT_MAX_WHOLE_CHARS
    window_chars: int = DEFAULT_WINDOW_CHARS
    overlap_chars: int = DEFAULT_OVERLAP_CHARS

    def __post_init__(self) -> None:
        if self.overlap_chars < 1:
            raise ValueError("overlap_chars must be at least 1")
        if self.window_chars < _MIN_WINDOW_TO_OVERLAP * self.overlap_chars:
            raise ValueError(
                f"window_chars must be at least {_MIN_WINDOW_TO_OVERLAP} times overlap_chars"
            )
        if self.max_whole_chars < 0:
            raise ValueError("max_whole_chars must not be negative")

    @property
    def slack(self) -> int:
        """How far a cut may move to land on a preferred boundary."""
        return max(1, self.window_chars // 10)


@dataclass(frozen=True)
class TextWindow:
    """One window ``[start, end)``; ``first``/``last`` mark the text edges (no cut there)."""

    start: int
    end: int
    first: bool
    last: bool


def _is_mark(char: str) -> bool:
    return unicodedata.category(char).startswith("M") or char == "\u200d"


def _hard_cut(text: str, position: int, floor: int) -> int:
    """``position`` moved back so it does not split a combining sequence."""
    while position > floor and _is_mark(text[position]):
        position -= 1
    return position


def _cut_at_or_before(
    text: str, target: int, floor: int, hints: Sequence[int], hint_floor: int
) -> int:
    """Best cut in ``(floor, target]``: block hint, then newline, then whitespace, then hard."""
    index = bisect_right(hints, target) - 1
    if index >= 0 and hints[index] > hint_floor:
        return hints[index]
    newline = text.rfind("\n", floor, target)
    if newline >= 0:
        return newline + 1
    for position in range(target - 1, floor - 1, -1):
        if text[position].isspace():
            return position + 1
    return _hard_cut(text, target, floor)


def plan_text_windows(
    text: str,
    policy: WindowingPolicy,
    boundaries: Sequence[int] = (),
) -> list[TextWindow]:
    """Cut ``text`` into overlapping windows that end and start on preferred boundaries.

    ``boundaries`` are optional offsets into ``text`` (block or paragraph
    ends); a cut lands on one when it lies in the second half of a window,
    otherwise on a newline, then any whitespace, within ``policy.slack``
    characters, and last at the exact position (never inside a combining
    sequence). Consecutive windows share at least ``policy.overlap_chars``.
    A text no longer than ``policy.window_chars`` is one window.
    """
    length = len(text)
    if length <= policy.window_chars:
        return [TextWindow(0, length, True, True)]
    hints = sorted({b for b in boundaries if 0 < b < length})
    windows: list[TextWindow] = []
    start = 0
    while True:
        target = start + policy.window_chars
        if target >= length:
            windows.append(TextWindow(start, length, not windows, True))
            return windows
        end = _cut_at_or_before(
            text, target, target - policy.slack, hints, start + policy.window_chars // 2
        )
        windows.append(TextWindow(start, end, not windows, False))
        wanted = end - policy.overlap_chars
        start = _cut_at_or_before(
            text, wanted, wanted - policy.slack, hints, wanted - policy.slack
        )


def _touches_cut(span: DetectedSpan, window: TextWindow) -> bool:
    return (not window.first and span.start <= window.start) or (
        not window.last and span.end >= window.end
    )


def _shifted(span: DetectedSpan, offset: int) -> DetectedSpan:
    core = None if span.core is None else (span.core[0] + offset, span.core[1] + offset)
    return DetectedSpan(
        start=span.start + offset,
        end=span.end + offset,
        entity_type=span.entity_type,
        score=span.score,
        core=core,
    )


def _seen_whole(span: DetectedSpan, windows: Sequence[TextWindow]) -> bool:
    """Whether ``span`` lies inside some window without touching its cut edges."""
    return any(
        window.start <= span.start
        and span.end <= window.end
        and not _touches_cut(span, window)
        for window in windows
    )


def merge_window_spans(
    windows: Sequence[TextWindow],
    window_spans: Sequence[Sequence[DetectedSpan]],
    full_text_spans: Sequence[DetectedSpan] = (),
) -> list[DetectedSpan]:
    """Join per-window spans (window-local offsets) into full-text spans.

    Spans touching a cut edge are dropped when a span that is whole in some
    window contains them; identical spans seen by two windows are kept once
    (highest score). ``full_text_spans`` (full-text offsets) come from layers
    that ran on the whole text as well as on every window: one lying inside a
    window without touching its cut is dropped, since that window reported it
    with the window's own score and filters; the rest count as whole. The
    result is a candidate list for overlap resolution, not a resolved one.
    """
    whole: list[DetectedSpan] = []
    cut: list[DetectedSpan] = []
    for window, spans in zip(windows, window_spans, strict=True):
        for span in spans:
            moved = _shifted(span, window.start)
            (cut if _touches_cut(moved, window) else whole).append(moved)
    whole.extend(s for s in full_text_spans if not _seen_whole(s, windows))

    whole.sort(key=lambda s: (s.start, s.end))
    starts = [s.start for s in whole]
    furthest_end: list[int] = []
    for span in whole:
        furthest_end.append(max(span.end, furthest_end[-1]) if furthest_end else span.end)
    kept = list(whole)
    for fragment in cut:
        candidates = bisect_right(starts, fragment.start)
        if candidates == 0 or furthest_end[candidates - 1] < fragment.end:
            kept.append(fragment)

    best: dict[tuple[int, int, str, tuple[int, int] | None], DetectedSpan] = {}
    for span in kept:
        key = (span.start, span.end, span.entity_type, span.core)
        current = best.get(key)
        if current is None or span.score > current.score:
            best[key] = span
    return sorted(best.values(), key=lambda s: (s.start, s.end, s.entity_type))
