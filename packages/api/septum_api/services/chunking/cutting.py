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
from typing import Any, Callable, Dict, Iterable, List, Sequence, Tuple

from septum_core.text_boundaries import BOUNDARY_CHARS, is_script_break

LengthFn = Callable[[str], int]

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
        length_fn: LengthFn,
        cap: int,
    ) -> None:
        self._text = text
        self._ranges = merge_ranges(protected, len(text))
        self._starts = [a for a, _ in self._ranges]
        self._length_fn = length_fn
        self._cap = cap
        self._ratio = 0.0

    def is_valid(self, offset: int) -> bool:
        """Whether ``offset`` is outside every protected range (range edges are fine)."""
        i = bisect_left(self._starts, offset) - 1
        return i < 0 or self._ranges[i][1] <= offset

    def _level(self, i: int) -> int:
        """Boundary quality at offset ``i`` (0 when ``i`` is no boundary)."""
        text = self._text
        prev = text[i - 1]
        if prev == "\n" or prev in _SPACELESS_END:
            return _LEVEL_SENTENCE
        if prev.isspace():
            j = i - 2
            while j >= 0 and text[j].isspace():
                if text[j] == "\n":
                    return _LEVEL_SENTENCE
                j -= 1
            if j >= 0 and text[j] in _SENTENCE_END:
                return _LEVEL_SENTENCE
            return _LEVEL_WHITESPACE
        if prev in BOUNDARY_CHARS or is_script_break(text, i):
            return _LEVEL_WORD
        return 0

    def _fit(self, s: int, hi: int, budget: int) -> int:
        """Largest end ``e`` in ``(s, hi]`` whose text fits ``budget`` and the character cap.

        Measures only near an estimate of where the budget runs out (the
        characters-per-unit ratio of the previous chunk), widens or narrows
        geometrically, then bisects the bracket, so a chunk costs a handful
        of probes of about its own size however long the text is.
        """
        limit = min(hi, s + self._cap)
        if self._length_fn is len:
            return max(s + 1, min(limit, s + budget))
        text, measure = self._text, self._length_fn
        sizes: Dict[int, int] = {}

        def fits(e: int) -> bool:
            sizes[e] = measure(text[s:e])
            return sizes[e] <= budget

        good, bad = s + 1, limit + 1
        probe = max(s + 1, min(limit, s + int(budget * (self._ratio or 1.0)) + 1))
        for refine in (True, False):
            up = fits(probe)
            if up:
                good = max(good, probe)
            else:
                bad = min(bad, probe)
            aim = s + int((probe - s) * budget / sizes[probe]) if sizes[probe] > 0 else 0
            if not refine or not good < aim < bad:
                break
            probe = aim
        step, gallop = 1, True
        while bad - good > 1:
            if gallop:
                nxt = good + step if up else bad - step
                nxt = min(max(nxt, good + 1), bad - 1)
            else:
                nxt = (good + bad) // 2
            if fits(nxt):
                good = nxt
                gallop = gallop and up
            else:
                bad = nxt
                gallop = gallop and not up
            step *= 2
        measured = sizes.get(good, 0)
        if measured > 0:
            self._ratio = (good - s) / measured
        return good

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

        Aims ``overlap`` characters before ``c``, moves forward to a word
        boundary, and never lands inside a protected range.
        """
        p = c - overlap
        if p <= s:
            return c
        for q in range(p, c):
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
        """Cover ``[lo, hi)`` with ranges of at most ``budget`` length units.

        ``anchors`` are preferred cuts (block boundaries); the largest valid
        anchor that fits wins. Inside a stretch with no anchor, cuts follow
        the priority in the module docstring and consecutive ranges overlap
        by up to ``overlap`` characters. A cut moves the window forward by at
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
                nxt = self._next_start(s, c, ov)
            if c >= hi:
                out.append((s, hi))
                return out
            out.append((s, c))
            s, last = nxt, c
