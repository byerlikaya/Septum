"""Where a chunk may end: boundaries, protected ranges and the packing loop.

Pure text arithmetic. A cut is a document offset ``c``: the chunk before it
ends at ``c`` and the next one starts at or before ``c``. A cut is never
strictly inside a protected range.

Cut priority, best first: a block boundary (the ``anchors`` handed to
:meth:`Cutter.pack`), a sentence boundary, a whitespace boundary, a word or
script boundary from ``septum_core.text_boundaries``, and a hard cut at the
budget only when none of those lies in the allowed window.

Protected-range edge case: a range that begins exactly where a chunk starts
and is longer than the budget makes the chunk grow to contain it. The chunk
then exceeds the budget, and exceeds the hard character cap too when the
range itself is longer than the cap; that is the only way a chunk can.
"""

from __future__ import annotations

import operator
from bisect import bisect_left, bisect_right
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from septum_core.text_boundaries import BOUNDARY_CHARS, is_script_break

_SENTENCE_END = frozenset(".!?…。！？।؟۔։።")
_SPACELESS_END = frozenset("。！？")

_LEVEL_SENTENCE = 1
_LEVEL_WHITESPACE = 2
_LEVEL_WORD = 3


def _index(value: Any) -> int:
    """``value`` as an ``int``; ``TypeError`` for floats, bools and non-numbers."""
    if isinstance(value, bool):
        raise TypeError("range bounds must be integers, not bool")
    return operator.index(value)


def merge_ranges(ranges: Iterable[Tuple[int, int]], length: int) -> List[Tuple[int, int]]:
    """Sorted, empty ones dropped, overlapping ones merged.

    ``ValueError`` unless every range satisfies ``0 <= start <= end <= length``;
    the message carries offsets only, never document text.
    """
    clean: List[Tuple[int, int]] = []
    for raw_a, raw_b in ranges:
        a, b = _index(raw_a), _index(raw_b)
        if not 0 <= a <= b <= length:
            raise ValueError(
                f"protected range [{a}, {b}) is invalid for text of length {length}"
            )
        if a < b:
            clean.append((a, b))
    clean.sort()
    merged: List[Tuple[int, int]] = []
    for a, b in clean:
        if merged and a < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


class Cutter:
    """Chooses cuts in one text for a set of protected ranges."""

    def __init__(
        self,
        text: str,
        protected: Iterable[Tuple[int, int]],
        cap: int,
    ) -> None:
        self._text = text
        self._ranges = merge_ranges(protected, len(text))
        self._starts = [a for a, _ in self._ranges]
        self._cap = cap
        self._run_lo: List[int] = []
        self._run_hi: List[int] = []
        self._run_ends: List[bool] = []

    def is_valid(self, offset: int) -> bool:
        """Whether ``offset`` is outside every protected range (range edges are fine)."""
        i = bisect_left(self._starts, offset) - 1
        return i < 0 or self._ranges[i][1] <= offset

    def next_valid(self, offset: int) -> int:
        """Smallest offset at or after ``offset`` outside every protected range."""
        i = bisect_left(self._starts, offset) - 1
        if i >= 0 and self._ranges[i][1] > offset:
            return self._ranges[i][1]
        return offset

    def _ends_sentence(self, j: int) -> bool:
        """Whether the whitespace run ending at ``j`` follows a newline or sentence end.

        Each walked run is remembered as one ``[lo, hi]`` record (sorted by
        ``lo``), so a long run is walked once however many offsets inside it
        are asked about, and memory grows with the number of runs, not their
        length.
        """
        text, los, his, ends = self._text, self._run_lo, self._run_hi, self._run_ends
        k = bisect_right(los, j) - 1
        floor = his[k] if k >= 0 else -1
        if k >= 0 and j <= floor:
            return ends[k]
        top = j
        while j > floor and text[j] != "\n" and text[j].isspace():
            j -= 1
        if j > floor:
            result = text[j] == "\n" or text[j] in _SENTENCE_END
        else:
            result = ends[k] if k >= 0 else False
        if j == top:
            return result
        if j <= floor and k >= 0:
            his[k] = top
        else:
            los.insert(k + 1, j + 1)
            his.insert(k + 1, top)
            ends.insert(k + 1, result)
        return result

    def _level(self, i: int) -> int:
        """Boundary quality at offset ``i`` (0 when ``i`` is no boundary)."""
        text = self._text
        prev = text[i - 1]
        if prev == "\n" or prev in _SPACELESS_END:
            return _LEVEL_SENTENCE
        if prev.isspace():
            return _LEVEL_SENTENCE if self._ends_sentence(i - 2) else _LEVEL_WHITESPACE
        if prev in BOUNDARY_CHARS or is_script_break(text, i):
            return _LEVEL_WORD
        return 0

    def _fit(self, s: int, hi: int, budget: int) -> int:
        """Largest end ``e`` in ``(s, hi]`` within ``budget`` characters and the character cap."""
        return max(s + 1, min(hi, s + self._cap, s + budget))

    def _cut(self, s: int, e: int, lowest: int) -> int:
        """Best cut in ``[lowest, e]``, else a hard cut at ``e`` moved out of any range."""
        found: Dict[int, int] = {}
        for i in range(e, lowest - 1, -1):
            if not self.is_valid(i):
                continue
            level = self._level(i)
            if level == _LEVEL_SENTENCE:
                return i
            if level and level not in found:
                found[level] = i
        for level in (_LEVEL_WHITESPACE, _LEVEL_WORD):
            if level in found:
                return found[level]
        if self.is_valid(e):
            return e
        a, b = self._ranges[bisect_left(self._starts, e) - 1]
        return a if a > s else b

    def _next_start(self, s: int, c: int, overlap: int) -> int:
        """Start of the chunk after one that began at ``s`` and was cut at ``c``.

        Aims ``overlap`` characters before ``c``. In the first half of that
        window it takes the earliest boundary of the best level (sentence,
        then whitespace, then word); with none there, the first boundary of
        any level in the rest of the window, else the aimed offset. The start
        never lands inside a protected range.
        """
        p = c - overlap
        if p <= s:
            return c
        half = min(p + overlap // 2 + 1, c)
        best = _LEVEL_WORD + 1
        pick = -1
        for q in range(p, half):
            if not self.is_valid(q):
                continue
            level = self._level(q)
            if level == _LEVEL_SENTENCE:
                return q
            if level and level < best:
                best, pick = level, q
        if pick >= 0:
            return pick
        for q in range(half, c):
            if self.is_valid(q) and self._level(q):
                return q
        if self.is_valid(p):
            return p
        a, b = self._ranges[bisect_left(self._starts, p) - 1]
        return a if a > s else b

    def pack(
        self,
        lo: int,
        hi: int,
        anchors: Sequence[int],
        budget: int,
        overlap: int,
    ) -> List[Tuple[int, int]]:
        """Cover ``[lo, hi)`` with ranges of at most ``budget`` characters.

        ``anchors`` are preferred cuts (block boundaries); the largest valid
        anchor that fits wins. Inside a stretch with no anchor, cuts follow
        the priority in the module docstring and consecutive ranges overlap
        by up to ``overlap`` characters, at most half the range. A cut moves the window forward by at
        least half of the nominal step (``budget - overlap``).
        """
        usable = sorted({a for a in anchors if lo < a < hi and self.is_valid(a)})
        out: List[Tuple[int, int]] = []
        s = last = lo
        while True:
            e = self._fit(s, hi, budget)
            if e >= hi:
                out.append((s, hi))
                return out
            k = bisect_right(usable, e) - 1
            if k >= 0 and usable[k] > last:
                c = nxt = usable[k]
            else:
                span = e - s
                ov = min(overlap, span // 2)
                c = self._cut(s, e, max(last + 1, s + ov + max(1, (span - ov) // 2)))
                if c <= last:
                    s = last
                    continue
                nxt = self._next_start(s, c, min(ov, c - s))
            if c >= hi:
                out.append((s, hi))
                return out
            out.append((s, c))
            s, last = nxt, c
