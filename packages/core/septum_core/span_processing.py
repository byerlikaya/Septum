from __future__ import annotations

"""
Span processing utilities for PII detection.

Provides deduplication, expansion and merging logic for detected PII spans.
These functions are stateless and operate on lists of ``DetectedSpan`` objects.
"""

import math
import unicodedata
from bisect import bisect_left, bisect_right, insort
from dataclasses import dataclass
from typing import List, Set

from .spans import DetectedSpan, clip_core
from .text_boundaries import is_script_break
from .text_utils import APOSTROPHES

# Entity-type priority used as a tiebreaker inside ``deduplicate_spans``.
# Higher number wins when two spans cover exactly the same offsets.
# The intent is to stop loose numeric recognizers (PHONE_NUMBER) from
# capturing identifiers that deserve a more specific label
# (NATIONAL_ID, IBAN, credit card). The absolute numbers here are not
# significant — only the relative ordering matters.
_ENTITY_TYPE_PRIORITY: dict[str, int] = {
    "CREDIT_CARD_NUMBER": 100,
    "IBAN": 100,
    "CPF": 95,
    "NATIONAL_ID": 95,
    "SOCIAL_SECURITY_NUMBER": 95,
    "HEALTH_INSURANCE_ID": 90,
    "MEDICAL_RECORD_NUMBER": 90,
    "PASSPORT_NUMBER": 90,
    "TAX_ID": 85,
    "DRIVERS_LICENSE": 80,
    "LICENSE_PLATE": 75,
    "PHONE_NUMBER": 70,
}


# Winner order for partial overlaps, used after ``_ENTITY_TYPE_PRIORITY``.
# Structured identifiers that a generic layer may only fragment (an email
# or URL that a NER PERSON or LOCATION span clips) outrank the generic
# types. Types not listed here default to 50.
_PARTIAL_OVERLAP_PRIORITY: dict[str, int] = {
    "EMAIL_ADDRESS": 60,
    "URL": 55,
    "IP_ADDRESS": 55,
    "MAC_ADDRESS": 55,
}


# Domain-aware absorption rules: when a span of the "inner" type
# overlaps a span of the "outer" type, the overlapped characters are
# removed from the inner span and the outer wins. The inner part that
# lies outside the outer span is kept, so no character goes unmasked.
# Example: in "ANTALYA SAĞLIK MERKEZİ" the LOCATION span "Antalya"
# overlaps the ORGANIZATION_NAME span "Antalya Sağlık Merkezi".
# Without this filter, length-based dedup picks LOCATION whenever it
# starts earlier than the ORG, which strips the richer ORG label
# and emits two placeholders for one semantic unit.
_ABSORPTION_RULES: dict[str, frozenset[str]] = {
    "DATE_OF_BIRTH": frozenset({"DATE"}),
    "ORGANIZATION_NAME": frozenset({"LOCATION"}),
    "POSTAL_ADDRESS": frozenset({"LOCATION", "STREET_ADDRESS"}),
    "STREET_ADDRESS": frozenset({"LOCATION"}),
}


def absorb_overlapping_spans(
    spans: List[DetectedSpan], text: str | None = None
) -> List[DetectedSpan]:
    """Trim inner spans around the outer spans that absorb them (``_ABSORPTION_RULES``).

    An inner span fully inside an outer span yields no remainder. A partly
    overlapping inner span keeps the part outside the outer span under its
    own type, so the outer span stays whole and the remainder is still
    masked. When ``text`` is given, offsets are clamped to it and remainders
    get the same trim and token fold as ``deduplicate_spans``. Spans with
    ``start >= end`` are discarded. A NaN or -inf score reads as 0.0 and +inf
    as 1.0.

    Rules run one layer per outer type, in ``_ABSORPTION_RULES`` order. The
    order that matters is POSTAL_ADDRESS before STREET_ADDRESS: a STREET_ADDRESS
    span is both an inner span of POSTAL_ADDRESS and an outer span of LOCATION,
    so POSTAL_ADDRESS trims it first and only then does it absorb its own inner
    LOCATION spans. Runs before ``deduplicate_spans`` so the longest-wins
    tiebreak does not split the richer outer span when an inner span happens
    to start at a lower offset.
    """
    cleaned = _clamped(spans, text)
    for outer_type, inner_types in _ABSORPTION_RULES.items():
        cleaned = _absorb_layer(cleaned, outer_type, inner_types, text)
    return cleaned


@dataclass
class _Grow:
    """A winner range that may widen over a token tail; ``core`` is the text it names."""

    start: int
    end: int
    core: tuple[int, int]

    def to_span(self, entity_type: str, score: float) -> DetectedSpan:
        """The span over this range, carrying ``core``."""
        return _make(self.start, self.end, entity_type, score, self.core)


class _Winners:
    """Winner ranges found by their edges while they widen.

    ``ending_at`` and ``starting_at`` return the first winner, in the order
    the winners were given, whose range ends or starts at a position now.
    ``widened`` turns true once any winner widens.
    """

    def __init__(self, winners: List[_Grow]) -> None:
        self.widened = False
        self._rank = {id(w): i for i, w in enumerate(winners)}
        self._by_end: dict[int, List[_Grow]] = {}
        self._by_start: dict[int, List[_Grow]] = {}
        for w in winners:
            self._by_end.setdefault(w.end, []).append(w)
            self._by_start.setdefault(w.start, []).append(w)

    def ending_at(self, position: int) -> _Grow | None:
        """The first winner whose range ends at ``position``."""
        found = self._by_end.get(position)
        return found[0] if found else None

    def starting_at(self, position: int) -> _Grow | None:
        """The first winner whose range starts at ``position``."""
        found = self._by_start.get(position)
        return found[0] if found else None

    def widen_end(self, winner: _Grow, end: int) -> None:
        """Move ``winner``'s end right to ``end``."""
        self._move(self._by_end, winner.end, end, winner)
        self._cover(winner.end, end)
        winner.end = end
        self.widened = True

    def widen_start(self, winner: _Grow, start: int) -> None:
        """Move ``winner``'s start left to ``start``."""
        self._move(self._by_start, winner.start, start, winner)
        self._cover(start, winner.start)
        winner.start = start
        self.widened = True

    def _move(
        self, edges: dict[int, List[_Grow]], old: int, new: int, winner: _Grow
    ) -> None:
        bucket = edges[old]
        del bucket[next(i for i, w in enumerate(bucket) if w is winner)]
        insort(edges.setdefault(new, []), winner, key=lambda w: self._rank[id(w)])

    def _cover(self, start: int, end: int) -> None:
        """Hook called with each range a winner widens over."""


class _CoveredWinners(_Winners):
    """``_Winners`` that also keep the union of their ranges.

    The union is a sorted list of disjoint ranges, merged when they touch,
    so the part of a range no winner covers is found by bisection.
    """

    def __init__(self, winners: List[_Grow]) -> None:
        super().__init__(winners)
        self._starts: List[int] = []
        self._ends: List[int] = []
        for w in sorted(winners, key=lambda w: w.start):
            self._cover(w.start, w.end)

    def _cover(self, start: int, end: int) -> None:
        first = bisect_left(self._ends, start)
        last = bisect_right(self._starts, end)
        if first < last:
            start = min(start, self._starts[first])
            end = max(end, self._ends[last - 1])
        self._starts[first:last] = [start]
        self._ends[first:last] = [end]

    def uncovered(self, start: int, end: int) -> List[tuple[int, int]]:
        """The parts of ``[start, end)`` that no winner covers, in order."""
        pieces: List[tuple[int, int]] = []
        cursor = start
        index = bisect_right(self._ends, start)
        while index < len(self._starts) and self._starts[index] < end:
            if self._starts[index] > cursor:
                pieces.append((cursor, self._starts[index]))
            cursor = self._ends[index]
            index += 1
        if cursor < end:
            pieces.append((cursor, end))
        return pieces


def _absorb_layer(
    spans: List[DetectedSpan],
    outer_type: str,
    inner_types: frozenset[str],
    text: str | None,
) -> List[DetectedSpan]:
    """Let every span of ``outer_type`` absorb the overlapping ``inner_types`` spans."""
    grown = {
        id(s): _Grow(s.start, s.end, _source_core(s))
        for s in spans
        if s.entity_type == outer_type
    }
    if not grown:
        return spans
    winners = _CoveredWinners(list(grown.values()))
    remainders = _settle_remainders(spans, inner_types, winners, text)

    result: List[DetectedSpan] = []
    for index, span in enumerate(spans):
        if span.entity_type == outer_type:
            result.append(grown[id(span)].to_span(span.entity_type, span.score))
        elif index in remainders:
            result.extend(remainders[index])
        else:
            result.append(span)
    return result


def _settle_remainders(
    spans: List[DetectedSpan],
    inner_types: frozenset[str],
    winners: _CoveredWinners,
    text: str | None,
) -> dict[int, List[DetectedSpan]]:
    """Repeat the inner-span cut until no winner widens, then return its remainders.

    An inner span handled before a later fold widens its outer would keep a
    remainder that now overlaps it, so the pass repeats until no outer grows.
    Each repeat only widens outers toward the text, so it terminates.
    """
    while True:
        winners.widened = False
        remainders = _cut_inner_spans(spans, inner_types, winners, text)
        if not winners.widened:
            return remainders


def _cut_inner_spans(
    spans: List[DetectedSpan],
    inner_types: frozenset[str],
    winners: _CoveredWinners,
    text: str | None,
) -> dict[int, List[DetectedSpan]]:
    """Map each inner span's index to the pieces left once ``winners`` absorb its overlap."""
    remainders: dict[int, List[DetectedSpan]] = {}
    for index, span in enumerate(spans):
        if span.entity_type not in inner_types:
            continue
        pieces = winners.uncovered(span.start, span.end)
        if pieces == [(span.start, span.end)]:
            remainders[index] = [span]
            continue
        if text is not None:
            pieces = [_claim_tails(text, s, e, winners) for s, e in pieces]
        remainders[index] = _remainders(span, pieces, text)
    return remainders


def deduplicate_spans(
    spans: List[DetectedSpan],
    high_priority_types: Set[str],
    text: str | None = None,
) -> List[DetectedSpan]:
    """Deduplicate overlapping spans without losing any covered character.

    High-priority entity types (for example ``PHONE_NUMBER``, ``NATIONAL_ID``,
    ``IBAN``) always win over more generic entities such as PERSON or
    LOCATION when their spans overlap.

    Spans with ``start >= end`` are discarded. A NaN or -inf score reads as
    0.0 and +inf as 1.0. When ``text`` is given, offsets
    are first clamped to ``[0, len(text)]``. Spans are then admitted in the
    order: high-priority pool first, then start offset, then larger span, then
    entity-type priority (see ``_ENTITY_TYPE_PRIORITY``), then recognizer score.
    A span fully covered by spans already kept is dropped. A span that only
    partly overlaps kept spans keeps its uncovered remainder under its own
    type, so no alphanumeric character of any input span is left unmasked.
    A newcomer that outranks every span it overlaps on ``_partial_rank`` (for
    example an EMAIL beating a PERSON fragment, or a NATIONAL_ID beating a
    PHONE) is kept whole and the overlapped spans are trimmed around it.
    Overlapping spans of one entity type merge into a single union span with
    the highest score. When ``text`` is given, remainders are trimmed of
    leading and trailing non-alphanumeric characters and dropped if nothing
    alphanumeric is left. A remainder that cuts inside a token (the "nın" of
    "Kaya'nın" after a PERSON span ends at "Kaya") is folded into the winner
    up to the token boundary instead of becoming a separate span, provided
    the folded part opens with a letter (a digit run stays separate). The fold
    widens the masked range only: the winner's ``core`` keeps the text it had
    before the fold, which is what the anonymization map registers.

    A loose 10-digit PHONE_NUMBER pattern at 0.80 must not silently beat a
    NATIONAL_ID detector at 0.60 on the exact same offsets, which is why the
    entity-type priority sits before the score in the order above.
    """

    cleaned = _clamped(spans, text)
    chosen: List[DetectedSpan] = []
    for high_priority in (True, False):
        pool = sorted(
            (s for s in cleaned if (s.entity_type in high_priority_types) == high_priority),
            key=_admission_order,
        )
        chosen = _sweep_pool(chosen, pool, high_priority_types, text)
    return chosen


def _admission_order(span: DetectedSpan) -> tuple[int, int, int, float]:
    """Admission order inside one pool: start, larger span, type priority, score."""
    return (
        span.start,
        -(span.end - span.start),
        -_ENTITY_TYPE_PRIORITY.get(span.entity_type, 50),
        -span.score,
    )


def _sweep_pool(
    chosen: List[DetectedSpan],
    newcomers: List[DetectedSpan],
    high_priority_types: Set[str],
    text: str | None,
) -> List[DetectedSpan]:
    """Admit ``newcomers``, in start order, into the disjoint ``chosen`` spans.

    ``chosen`` stays sorted by start and its spans never overlap, so the spans
    a newcomer overlaps are the next ones of a sweep. ``ahead`` holds the
    spans that end after the newcomer's start, nearest last; ``done`` holds
    the spans before it, which no later newcomer of the pool can reach.
    """
    done: List[DetectedSpan] = []
    ahead = chosen[::-1]
    for span in newcomers:
        while ahead and ahead[-1].end <= span.start:
            done.append(ahead.pop())
        overlapped: List[DetectedSpan] = []
        while ahead and ahead[-1].start < span.end:
            overlapped.append(ahead.pop())
        placed = sorted(
            _admit(span, overlapped, high_priority_types, text), key=lambda s: s.start
        )
        done.extend(s for s in placed if s.end <= span.start)
        ahead.extend(reversed([s for s in placed if s.end > span.start]))
    return done + ahead[::-1]


def _finite_score(score: float) -> float:
    """``score`` with NaN and -inf read as 0.0 and +inf as 1.0.

    A NaN compares false with everything, so it would make the admission sort
    and the union's ``max`` depend on input order. The span is kept either way.
    """
    if math.isfinite(score):
        return score
    return 1.0 if score > 0 else 0.0


def _clamped(spans: List[DetectedSpan], text: str | None) -> List[DetectedSpan]:
    """Copy ``spans`` with offsets clamped to ``text``, scores made finite, empty ones dropped."""
    limit = len(text) if text is not None else None
    cleaned: List[DetectedSpan] = []
    for span in spans:
        start, end = span.start, span.end
        if limit is not None:
            start = min(max(start, 0), limit)
            end = min(max(end, 0), limit)
        if start < end:
            cleaned.append(
                _make(
                    start,
                    end,
                    span.entity_type,
                    _finite_score(span.score),
                    _source_core(span),
                )
            )
    return cleaned


def _partial_rank(
    span: DetectedSpan, high_priority_types: Set[str]
) -> tuple[bool, int]:
    priority = _ENTITY_TYPE_PRIORITY.get(
        span.entity_type, _PARTIAL_OVERLAP_PRIORITY.get(span.entity_type, 50)
    )
    return (span.entity_type in high_priority_types, priority)


def _subtract(
    start: int, end: int, cuts: List[tuple[int, int]]
) -> List[tuple[int, int]]:
    """Return the parts of ``[start, end)`` not covered by any cut range."""
    pieces: List[tuple[int, int]] = []
    cursor = start
    for cut_start, cut_end in sorted(cuts):
        if cut_end <= cursor or cut_start >= end:
            continue
        if cut_start > cursor:
            pieces.append((cursor, cut_start))
        cursor = max(cursor, cut_end)
    if cursor < end:
        pieces.append((cursor, end))
    return pieces


def _is_mark(ch: str) -> bool:
    """A combining mark (category M), which belongs to the character before it."""
    return unicodedata.category(ch).startswith("M")


def _overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    """Whether the half-open ranges ``[a_start, a_end)`` and ``[b_start, b_end)`` meet."""
    return not (a_end <= b_start or a_start >= b_end)


def _source_core(span: DetectedSpan) -> tuple[int, int]:
    """The range that names ``span``'s entity: its ``core``, or the whole span."""
    return clip_core(span.start, span.end, span.core)


def _make(
    start: int,
    end: int,
    entity_type: str,
    score: float,
    core: tuple[int, int],
) -> DetectedSpan:
    """Build a span over ``[start, end)`` whose ``core`` is clipped to it.

    The core is dropped when it covers the whole span or nothing of it.
    """
    core_start, core_end = clip_core(start, end, core)
    if core_start >= core_end or (core_start, core_end) == (start, end):
        return DetectedSpan(start=start, end=end, entity_type=entity_type, score=score)
    return DetectedSpan(
        start=start,
        end=end,
        entity_type=entity_type,
        score=score,
        core=(core_start, core_end),
    )


def _joins_through_apostrophe(text: str, a: int) -> bool:
    """Whether the apostrophe at ``text[a]`` sits inside a token.

    It does when a word character lies on each side with no script break
    between them, so "Kaya'nın" joins and "Z’请" (an unspaced glyph after the
    apostrophe) does not. The apostrophe is never at the text edge.
    """
    return (
        0 < a < len(text) - 1
        and text[a] in APOSTROPHES
        and (text[a - 1].isalnum() or _is_mark(text[a - 1]))
        and (text[a + 1].isalnum() or _is_mark(text[a + 1]))
        and not is_script_break(text, a)
        and not is_script_break(text, a + 1)
    )


def _joins(text: str, p: int) -> bool:
    """Whether the edge before ``text[p]`` lies inside one token.

    A token is a run of word characters with no script break between them.
    An apostrophe between two word characters joins a suffix to its name
    ("Kaya'nın"), the same convention ``strip_possessive_suffix`` uses.
    """
    if not 0 < p < len(text):
        return False
    left, right = text[p - 1], text[p]
    if (left.isalnum() or _is_mark(left)) and (right.isalnum() or _is_mark(right)):
        return not is_script_break(text, p)
    if right in APOSTROPHES:
        return _joins_through_apostrophe(text, p)
    if left in APOSTROPHES:
        return _joins_through_apostrophe(text, p - 1)
    return False


def _claim_tails(
    text: str | None,
    start: int,
    end: int,
    winners: _Winners,
) -> tuple[int, int]:
    """Fold the part of ``[start, end)`` that continues a winner's token into it.

    ``winners`` widens mutable :class:`_Grow` ranges; only ``start`` and
    ``end`` are widened, ``core`` is left alone. A winner ending at
    ``start`` takes the leading tail of the token it cuts, and a winner
    starting at ``end`` takes the trailing one, up to the token boundary.
    A tail is folded only when it opens with a letter (see
    ``_tail_starts_with_letter``): a digit run after an apostrophe stays its
    own span. When several winners share a boundary the first one in
    ``winners`` takes the tail. Returns the part left over, which may be
    empty. Without ``text`` nothing is folded.
    """
    if text is None:
        return start, end
    start = _claim_leading_tail(text, start, end, winners)
    return start, _claim_trailing_tail(text, start, end, winners)


def _claim_leading_tail(
    text: str, start: int, end: int, winners: _Winners
) -> int:
    """Fold the leading token tail of ``[start, end)`` into the winner ending at ``start``."""
    left = winners.ending_at(start)
    if (
        left is None
        or not _joins(text, start)
        or not _tail_starts_with_letter(text, start)
    ):
        return start
    q = start + 1
    while q < end and _joins(text, q):
        q += 1
    winners.widen_end(left, q)
    return q


def _claim_trailing_tail(
    text: str, start: int, end: int, winners: _Winners
) -> int:
    """Fold the trailing token tail of ``[start, end)`` into the winner starting at ``end``."""
    right = winners.starting_at(end)
    if right is None or start >= end or not _joins(text, end):
        return end
    k = end - 1
    while k > start and _joins(text, k):
        k -= 1
    if not _tail_starts_with_letter(text, k):
        return end
    winners.widen_start(right, k)
    return k


def _tail_starts_with_letter(text: str, p: int) -> bool:
    """Whether the tail beginning at ``text[p]`` opens with a letter.

    An apostrophe may come first ("’nın"); the letter must follow it. A
    letter is ``isalpha`` or a combining mark, so a digit run does not count.
    """
    if p < len(text) and text[p] in APOSTROPHES:
        p += 1
    return p < len(text) and (text[p].isalpha() or _is_mark(text[p]))


def _trim_to_alnum(text: str, start: int, end: int) -> tuple[int, int]:
    """Shrink ``[start, end)`` past leading and trailing characters that are not letters or digits."""
    while start < end and not (text[start].isalnum() or _is_mark(text[start])):
        start += 1
    while end > start and not (text[end - 1].isalnum() or _is_mark(text[end - 1])):
        end -= 1
    return start, end


def _remainders(
    span: DetectedSpan,
    ranges: List[tuple[int, int]],
    text: str | None,
) -> List[DetectedSpan]:
    result: List[DetectedSpan] = []
    for start, end in ranges:
        if text is not None:
            start, end = _trim_to_alnum(text, start, end)
        if start >= end or (
            text is not None and not any(ch.isalnum() for ch in text[start:end])
        ):
            continue
        result.append(
            _make(start, end, span.entity_type, span.score, _source_core(span))
        )
    return result


def _admit(
    span: DetectedSpan,
    overlapped: List[DetectedSpan],
    high_priority_types: Set[str],
    text: str | None,
) -> List[DetectedSpan]:
    """The spans that replace ``overlapped`` once ``span`` is admitted among them."""
    if not overlapped:
        return [span]
    newcomer_rank = _partial_rank(span, high_priority_types)

    if all(c.entity_type == span.entity_type for c in overlapped):
        return [_admit_union(span, overlapped)]
    if all(newcomer_rank > _partial_rank(c, high_priority_types) for c in overlapped):
        return _admit_whole(span, overlapped, text)
    return _admit_remainder(span, overlapped, text)


def _admit_union(span: DetectedSpan, overlapped: List[DetectedSpan]) -> DetectedSpan:
    """The union of ``span`` and the same-type ``overlapped`` spans."""
    members = [span, *overlapped]
    cores = [_source_core(m) for m in members]
    return _make(
        min(m.start for m in members),
        max(m.end for m in members),
        span.entity_type,
        max(m.score for m in members),
        (min(c[0] for c in cores), max(c[1] for c in cores)),
    )


def _admit_whole(
    span: DetectedSpan,
    overlapped: List[DetectedSpan],
    text: str | None,
) -> List[DetectedSpan]:
    """``span`` kept whole, with the lower-ranked ``overlapped`` spans trimmed around it."""
    winner = _Grow(span.start, span.end, _source_core(span))
    edges = _Winners([winner])
    kept: List[DetectedSpan] = []
    for c in overlapped:
        for start, end in _subtract(c.start, c.end, [(span.start, span.end)]):
            start, end = _claim_tails(text, start, end, edges)
            kept.extend(_remainders(c, [(start, end)], text))
    kept.append(winner.to_span(span.entity_type, span.score))
    return kept


def _admit_remainder(
    span: DetectedSpan,
    overlapped: List[DetectedSpan],
    text: str | None,
) -> List[DetectedSpan]:
    """The ``overlapped`` spans kept, plus only the part of ``span`` they leave."""
    winners = [_Grow(c.start, c.end, _source_core(c)) for c in overlapped]
    edges = _Winners(winners)
    gaps = [
        _claim_tails(text, start, end, edges)
        for start, end in _subtract(
            span.start, span.end, [(c.start, c.end) for c in overlapped]
        )
    ]
    kept = [w.to_span(c.entity_type, c.score) for w, c in zip(winners, overlapped)]
    return kept + _remainders(span, gaps, text)


def _find_token_start(text: str, idx: int) -> int:
    """Walk left from ``idx`` to the start of the token."""
    while idx > 0 and not text[idx - 1].isspace() and not is_script_break(text, idx):
        idx -= 1
    return idx


def _find_token_end(text: str, idx: int) -> int:
    """Walk right from ``idx`` to the end of the token."""
    n = len(text)
    while idx < n and not text[idx].isspace() and not is_script_break(text, idx):
        idx += 1
    return idx


def _is_name_like_token(text: str, start: int, end: int) -> bool:
    """Whether ``text[start:end]`` starts with an uppercase letter and has no digit or ``_``."""
    token = text[start:end]
    if not token:
        return False
    if not token[0].isalpha() or not token[0].isupper():
        return False
    return not any(ch.isdigit() or ch == "_" for ch in token)


def _scan_horizontal_ws(text: str, idx: int, step: int) -> tuple[int, bool]:
    """Skip horizontal whitespace from ``idx`` in ``step`` direction.

    Returns the post-scan index and whether a newline was hit. Form
    labels always sit on a line above/below their value, so any
    expansion crossing ``\\n`` would absorb the wrong row.
    """
    peek = 0 if step > 0 else -1
    while (idx < len(text)) if step > 0 else (idx > 0):
        ch = text[idx + peek]
        if not ch.isspace():
            break
        if ch in ("\n", "\r"):
            return idx, True
        idx += step
    return idx, False


def _free_name_token(
    text: str,
    start: int,
    end: int,
    occupied_ranges: List[tuple[int, int]],
) -> bool:
    """Whether ``text[start:end]`` is an unclaimed name-like word of two or more letters."""
    token = text[start:end]
    return (
        _is_name_like_token(text, start, end)
        and len(token) >= 2
        and token.isalpha()
        and not any(_overlaps(start, end, s, e) for s, e in occupied_ranges)
    )


def _right_surname_end(
    text: str, end: int, occupied_ranges: List[tuple[int, int]]
) -> int:
    """The end of the single name token right of ``end``; ``end`` when there is none."""
    right, crossed_nl = _scan_horizontal_ws(text, end, 1)
    if crossed_nl or right >= len(text):
        return end
    right_end = _find_token_end(text, right)
    if _free_name_token(text, right, right_end, occupied_ranges):
        return right_end
    return end


def _left_name_start(
    text: str, start: int, occupied_ranges: List[tuple[int, int]]
) -> int:
    """The start of the single name token left of ``start``; ``start`` when there is none."""
    left, crossed_nl = _scan_horizontal_ws(text, start, -1)
    if crossed_nl or left <= 0:
        return start
    left_start = _find_token_start(text, left - 1)
    if _free_name_token(text, left_start, left, occupied_ranges):
        return left_start
    return start


def _expand_person_span(
    text: str,
    span: DetectedSpan,
    occupied_ranges: List[tuple[int, int]],
) -> DetectedSpan:
    """Widen one PERSON_NAME span over a neighbouring name token on each side."""
    start = span.start
    end = span.end

    if (
        not _is_name_like_token(text, start, end)
        or len(text[start:end].strip()) < 2
    ):
        return span

    # A side where the core stops short of the span carries a folded
    # suffix ("’nın"): that side is the end of the name, so nothing is
    # added past it.
    core_start, core_end = _source_core(span)

    if core_end == span.end:
        end = _right_surname_end(text, end, occupied_ranges)
    if core_start == span.start:
        start = _left_name_start(text, start, occupied_ranges)

    return _make(
        start,
        end,
        span.entity_type,
        span.score,
        (start if start < span.start else core_start,
         end if end > span.end else core_end),
    )


def expand_person_name_spans(
    text: str,
    spans: List[DetectedSpan],
) -> List[DetectedSpan]:
    """Expand PERSON_NAME spans to include adjacent capitalized tokens.

    When a PERSON_NAME span covers only part of a name (for example, a given
    name without the following surname), this helper inspects the immediate
    neighbouring tokens on both sides and extends the span to include them
    when they look like name tokens (letter-only, starting with uppercase).
    The heuristic is intentionally simple and language-agnostic.
    """
    if not spans or not text:
        return spans

    occupied_ranges = [(s.start, s.end) for s in spans]
    expanded = [
        _expand_person_span(text, span, occupied_ranges)
        if span.entity_type == "PERSON_NAME"
        else span
        for span in spans
    ]
    return sorted(expanded, key=lambda s: (s.start, -(s.end - s.start), -s.score))


def _joins_next_person(
    text: str, end: int, last: DetectedSpan, nxt: DetectedSpan
) -> bool:
    """Whether ``nxt`` continues the name ending at ``end`` across horizontal space only."""
    # A folded suffix ends the name: no merge past it, and no merge
    # into a span whose suffix sits before its core.
    if _source_core(last)[1] < last.end or _source_core(nxt)[0] > nxt.start:
        return False
    gap = text[end : nxt.start]
    return gap.strip() == "" and "\n" not in gap and "\r" not in gap


def _merge_person_run(
    text: str, person_spans: List[DetectedSpan], i: int
) -> tuple[DetectedSpan, int]:
    """Merge the run of person spans starting at ``i``; return it and the next index."""
    cur = person_spans[i]
    end = cur.end
    max_score = cur.score
    last = cur
    j = i + 1
    while j < len(person_spans) and _joins_next_person(
        text, end, last, person_spans[j]
    ):
        last = person_spans[j]
        end = last.end
        max_score = max(max_score, last.score)
        j += 1
    merged = _make(
        cur.start,
        end,
        "PERSON_NAME",
        max_score,
        (_source_core(cur)[0], _source_core(last)[1]),
    )
    return merged, j


def merge_adjacent_person_name_spans(
    text: str,
    spans: List[DetectedSpan],
) -> List[DetectedSpan]:
    """Merge consecutive PERSON_NAME spans separated only by horizontal whitespace.

    Prevents split replacements (e.g. given name and surname as two spans)
    from producing broken placeholder insertion inside a single visual name.
    """
    if not spans:
        return spans

    others = [s for s in spans if s.entity_type != "PERSON_NAME"]
    person_spans = sorted(
        [s for s in spans if s.entity_type == "PERSON_NAME"],
        key=lambda s: s.start,
    )
    if len(person_spans) <= 1:
        return sorted(spans, key=lambda s: s.start)

    merged_person: list[DetectedSpan] = []
    i = 0
    while i < len(person_spans):
        merged, i = _merge_person_run(text, person_spans, i)
        merged_person.append(merged)

    return sorted(others + merged_person, key=lambda s: s.start)
