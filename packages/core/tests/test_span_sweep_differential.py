"""Compare the indexed span resolution with the quadratic scan it replaced.

``absorb_overlapping_spans`` and ``deduplicate_spans`` find overlaps by
bisection and a sweep instead of checking every span against every other one.
This file keeps a private copy of the earlier scan and requires both to give
the same spans, in the same order, on a seeded corpus built to produce ties:
equal offsets, equal scores, different types on one range, nested, adjacent
and touching spans, and token tails that fold into a winner.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from typing import List, Set

import pytest
from septum_core import span_processing as sp
from septum_core.detector import _HIGH_PRIORITY_ENTITY_TYPES
from septum_core.spans import DetectedSpan

# ---------------------------------------------------------------------------
# Reference: the scan the indexed version replaced. Leaf helpers that did not
# change are shared with the module; everything that looked up overlaps or
# winner edges is copied here as it was.
# ---------------------------------------------------------------------------

_BRANCHES: Counter = Counter()


def _count_fold(winners_on_edge: int) -> None:
    _BRANCHES["fold"] += 1
    if winners_on_edge > 1:
        _BRANCHES["shared_edge_fold"] += 1


def _ref_leading(text: str, start: int, end: int, winners: list) -> int:
    left = next((w for w in winners if w.end == start), None)
    if left is None or not sp._joins(text, start) or not sp._tail_starts_with_letter(text, start):
        return start
    q = start + 1
    while q < end and sp._joins(text, q):
        q += 1
    _count_fold(sum(1 for w in winners if w.end == start))
    left.end = q
    return q


def _ref_trailing(text: str, start: int, end: int, winners: list) -> int:
    right = next((w for w in winners if w.start == end), None)
    if right is None or start >= end or not sp._joins(text, end):
        return end
    k = end - 1
    while k > start and sp._joins(text, k):
        k -= 1
    if not sp._tail_starts_with_letter(text, k):
        return end
    _count_fold(sum(1 for w in winners if w.start == end))
    right.start = k
    return k


def _ref_claim(text: str | None, start: int, end: int, winners: list) -> tuple[int, int]:
    if text is None:
        return start, end
    start = _ref_leading(text, start, end, winners)
    return start, _ref_trailing(text, start, end, winners)


def _ref_cut(spans: list, inner_types: frozenset, winners: list, text: str | None) -> dict:
    _BRANCHES["cut_pass"] += 1
    remainders: dict = {}
    for index, span in enumerate(spans):
        if span.entity_type not in inner_types:
            continue
        cuts = [
            (w.start, w.end)
            for w in winners
            if sp._overlaps(span.start, span.end, w.start, w.end)
        ]
        if not cuts:
            remainders[index] = [span]
            continue
        pieces = sp._subtract(span.start, span.end, cuts)
        if text is not None:
            pieces = [_ref_claim(text, s, e, winners) for s, e in pieces]
        remainders[index] = sp._remainders(span, pieces, text)
    return remainders


def _ref_settle(spans: list, inner_types: frozenset, winners: list, text: str | None) -> dict:
    passes = 0
    while True:
        passes += 1
        before = [(w.start, w.end) for w in winners]
        remainders = _ref_cut(spans, inner_types, winners, text)
        if [(w.start, w.end) for w in winners] == before:
            break
    if passes > 1:
        _BRANCHES["repeat_pass"] += 1
    return remainders


def _ref_layer(spans: list, outer_type: str, inner_types: frozenset, text: str | None) -> list:
    grown = {
        id(s): sp._Grow(s.start, s.end, sp._source_core(s))
        for s in spans
        if s.entity_type == outer_type
    }
    if not grown:
        return spans
    remainders = _ref_settle(spans, inner_types, list(grown.values()), text)
    result = []
    for index, span in enumerate(spans):
        if span.entity_type == outer_type:
            result.append(grown[id(span)].to_span(span.entity_type, span.score))
        elif index in remainders:
            result.extend(remainders[index])
        else:
            result.append(span)
    return result


def _ref_absorb(spans: list, text: str | None = None) -> list:
    cleaned = sp._clamped(spans, text)
    for outer_type, inner_types in sp._ABSORPTION_RULES.items():
        cleaned = _ref_layer(cleaned, outer_type, inner_types, text)
    return cleaned


def _ref_union(chosen: list, span: DetectedSpan, overlapped: list, ids: Set[int]) -> None:
    members = [span, *overlapped]
    cores = [sp._source_core(m) for m in members]
    chosen[:] = [c for c in chosen if id(c) not in ids]
    chosen.append(
        sp._make(
            min(m.start for m in members),
            max(m.end for m in members),
            span.entity_type,
            max(m.score for m in members),
            (min(c[0] for c in cores), max(c[1] for c in cores)),
        )
    )


def _ref_whole(chosen: list, span: DetectedSpan, overlapped: list, ids: Set[int], text: str | None) -> None:
    winner = sp._Grow(span.start, span.end, sp._source_core(span))
    kept = [c for c in chosen if id(c) not in ids]
    for c in overlapped:
        for start, end in sp._subtract(c.start, c.end, [(span.start, span.end)]):
            start, end = _ref_claim(text, start, end, [winner])
            kept.extend(sp._remainders(c, [(start, end)], text))
    kept.append(winner.to_span(span.entity_type, span.score))
    chosen[:] = kept


def _ref_remainder(chosen: list, span: DetectedSpan, overlapped: list, ids: Set[int], text: str | None) -> None:
    winners = [sp._Grow(c.start, c.end, sp._source_core(c)) for c in overlapped]
    gaps = [
        _ref_claim(text, start, end, winners)
        for start, end in sp._subtract(
            span.start, span.end, [(c.start, c.end) for c in overlapped]
        )
    ]
    chosen[:] = [c for c in chosen if id(c) not in ids] + [
        w.to_span(c.entity_type, c.score) for w, c in zip(winners, overlapped)
    ]
    chosen.extend(sp._remainders(span, gaps, text))


def _ref_admit(chosen: list, span: DetectedSpan, high: Set[str], text: str | None) -> None:
    overlapped = [c for c in chosen if sp._overlaps(span.start, span.end, c.start, c.end)]
    if not overlapped:
        chosen.append(span)
        return
    ids = {id(c) for c in overlapped}
    rank = sp._partial_rank(span, high)
    if all(c.entity_type == span.entity_type for c in overlapped):
        _BRANCHES["union"] += 1
        _ref_union(chosen, span, overlapped, ids)
    elif all(rank > sp._partial_rank(c, high) for c in overlapped):
        _BRANCHES["whole"] += 1
        _ref_whole(chosen, span, overlapped, ids, text)
    else:
        _BRANCHES["remainder"] += 1
        _ref_remainder(chosen, span, overlapped, ids, text)


def _ref_dedupe(spans: list, high: Set[str], text: str | None = None) -> list:
    cleaned = sp._clamped(spans, text)
    ordered = sorted(
        cleaned,
        key=lambda s: (
            0 if s.entity_type in high else 1,
            s.start,
            -(s.end - s.start),
            -sp._ENTITY_TYPE_PRIORITY.get(s.entity_type, 50),
            -s.score,
        ),
    )
    chosen: list = []
    for span in ordered:
        _ref_admit(chosen, span, high, text)
    return sorted(chosen, key=lambda s: s.start)


# ---------------------------------------------------------------------------
# Corpus
# ---------------------------------------------------------------------------

# Apostrophe-joined tokens fold token tails into winners; combining marks and
# mixed scripts move token boundaries; digits keep a tail from folding.
_WORDS = (
    "Kaya’nın", "O'Brien", "Ankara'da", "Yılmaz", "ali", "x1", "12'si", "3rd",
    "İstanbul", "Zoë", "éclair", "你好", "ABC世界", "สวัสดี", "Merkezi", "'",
)
_GAPS = (" ", " ", " ", "", "'", "’", "-", ", ", "\n", "  ")
_TYPES = (
    "LOCATION", "LOCATION", "ORGANIZATION_NAME", "STREET_ADDRESS", "POSTAL_ADDRESS",
    "DATE", "DATE_OF_BIRTH", "PERSON_NAME", "PERSON_NAME", "PHONE_NUMBER",
    "NATIONAL_ID", "EMAIL_ADDRESS", "URL", "IBAN",
)
_SCORES = (0.5, 0.5, 0.8, 0.9)
_HIGH_SETS = (_HIGH_PRIORITY_ENTITY_TYPES, {"PHONE_NUMBER"}, set(), {"LOCATION", "DATE"})


def _text(rng: random.Random) -> tuple[str, list[int]]:
    parts: list[str] = []
    edges = [0]
    for _ in range(rng.randint(2, 14)):
        parts.append(rng.choice(_WORDS))
        parts.append(rng.choice(_GAPS))
        edges.append(len("".join(parts)))
    return "".join(parts), edges


def _offset(rng: random.Random, text: str, edges: list[int]) -> int:
    if rng.random() < 0.7:
        return rng.choice(edges)
    return rng.randint(-1, len(text) + 1)


def _range(
    rng: random.Random, spans: list[DetectedSpan], text: str, edges: list[int]
) -> tuple[int, int]:
    roll = rng.random()
    if spans and roll < 0.25:
        # Same offsets as an earlier span: a tie on start and end.
        base = rng.choice(spans)
        return base.start, base.end
    if spans and roll < 0.4:
        # Nested inside, or touching, an earlier span.
        base = rng.choice(spans)
        if rng.random() < 0.5 and base.end - base.start > 1:
            start = rng.randint(base.start, base.end - 1)
            return start, rng.randint(start + 1, base.end)
        return base.end, base.end + rng.randint(1, 9)
    start = _offset(rng, text, edges)
    end = _offset(rng, text, edges) if rng.random() < 0.6 else start + rng.randint(-1, 12)
    return min(start, end), max(start, end)


def _spans(rng: random.Random, text: str, edges: list[int]) -> list[DetectedSpan]:
    spans: list[DetectedSpan] = []
    for _ in range(rng.randint(1, 24)):
        start, end = _range(rng, spans, text, edges)
        core = None
        if end - start > 2 and rng.random() < 0.2:
            core = (start + rng.randint(0, 1), end - rng.randint(0, 2))
        spans.append(DetectedSpan(start, end, rng.choice(_TYPES), rng.choice(_SCORES), core))
    return spans


def _cases(count: int = 6000) -> list[tuple[str, list[DetectedSpan], Set[str]]]:
    rng = random.Random(55_2026)
    cases = []
    for index in range(count):
        text, edges = _text(rng)
        cases.append((text, _spans(rng, text, edges), _HIGH_SETS[index % len(_HIGH_SETS)]))
    return cases


def _copy(spans: List[DetectedSpan]) -> List[DetectedSpan]:
    return [DetectedSpan(s.start, s.end, s.entity_type, s.score, s.core) for s in spans]


def _rows(spans: List[DetectedSpan]) -> list[tuple]:
    return [(s.start, s.end, s.entity_type, s.score, s.core) for s in spans]


_CASES = _cases()


@pytest.mark.parametrize("use_text", [True, False], ids=["text", "no_text"])
def test_absorb_matches_the_scan(use_text: bool) -> None:
    for number, (text, spans, _) in enumerate(_CASES):
        given = text if use_text else None
        expected = _rows(_ref_absorb(_copy(spans), given))
        actual = _rows(sp.absorb_overlapping_spans(_copy(spans), given))
        assert actual == expected, f"case {number}: {text!r} {_rows(spans)}"


@pytest.mark.parametrize("use_text", [True, False], ids=["text", "no_text"])
def test_dedupe_matches_the_scan(use_text: bool) -> None:
    for number, (text, spans, high) in enumerate(_CASES):
        given = text if use_text else None
        expected = _rows(_ref_dedupe(_copy(spans), high, given))
        actual = _rows(sp.deduplicate_spans(_copy(spans), high, given))
        assert actual == expected, f"case {number}: {text!r} {_rows(spans)} {sorted(high)}"


def test_corpus_reaches_every_branch() -> None:
    """The corpus must take every admission branch, fold tails and repeat absorb passes."""
    _BRANCHES.clear()
    for text, spans, high in _CASES:
        _ref_absorb(_copy(spans), text)
        _ref_dedupe(_copy(spans), high, text)
    for branch in ("union", "whole", "remainder", "fold", "repeat_pass"):
        assert _BRANCHES[branch] >= 50, dict(_BRANCHES)
    assert _BRANCHES["shared_edge_fold"] >= 50, dict(_BRANCHES)


_COUNTED = ("union", "whole", "remainder", "fold", "shared_edge_fold", "cut_pass")


def _counting(counts: Counter, key: str, func):
    def wrapper(*args, **kwargs):
        counts[key] += 1
        return func(*args, **kwargs)

    return wrapper


def _counting_widen(counts: Counter, edge: str, func):
    def wrapper(self, winner, position):
        counts["fold"] += 1
        edges = self._by_end if edge == "end" else self._by_start
        if len(edges[getattr(winner, edge)]) > 1:
            counts["shared_edge_fold"] += 1
        return func(self, winner, position)

    return wrapper


def test_new_code_takes_the_same_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    """The indexed code admits, folds and repeats passes exactly as often as the scan."""
    counts: Counter = Counter()
    for name, key in (
        ("_admit_union", "union"),
        ("_admit_whole", "whole"),
        ("_admit_remainder", "remainder"),
        ("_cut_inner_spans", "cut_pass"),
    ):
        monkeypatch.setattr(sp, name, _counting(counts, key, getattr(sp, name)))
    monkeypatch.setattr(sp._Winners, "widen_end", _counting_widen(counts, "end", sp._Winners.widen_end))
    monkeypatch.setattr(sp._Winners, "widen_start", _counting_widen(counts, "start", sp._Winners.widen_start))
    _BRANCHES.clear()
    for text, spans, high in _CASES:
        _ref_absorb(_copy(spans), text)
        _ref_dedupe(_copy(spans), high, text)
        sp.absorb_overlapping_spans(_copy(spans), text)
        sp.deduplicate_spans(_copy(spans), high, text)
    assert {k: counts[k] for k in _COUNTED} == {k: _BRANCHES[k] for k in _COUNTED}


# ---------------------------------------------------------------------------
# Non-finite scores
# ---------------------------------------------------------------------------

_NON_FINITE = (math.nan, math.inf, -math.inf)


def _finite(score: float) -> float:
    if math.isnan(score) or score == -math.inf:
        return 0.0
    return 1.0 if score == math.inf else score


def _non_finite_cases(count: int = 2000) -> list[tuple[str, list[DetectedSpan], Set[str]]]:
    rng = random.Random(55_0001)
    cases = []
    for text, spans, high in _CASES[:count]:
        scored = [
            DetectedSpan(s.start, s.end, s.entity_type, rng.choice(_NON_FINITE), s.core)
            if rng.random() < 0.4
            else s
            for s in spans
        ]
        cases.append((text, scored, high))
    return cases


def _normalised(spans: List[DetectedSpan]) -> List[DetectedSpan]:
    return [DetectedSpan(s.start, s.end, s.entity_type, _finite(s.score), s.core) for s in spans]


_NON_FINITE_CASES = _non_finite_cases()


def _same(left: list[tuple], right: list[tuple]) -> bool:
    # repr compares NaN by spelling, so a stray NaN cannot pass as equal.
    return repr(left) == repr(right)


def test_non_finite_scores_match_the_scan_on_normalised_input() -> None:
    for number, (text, spans, high) in enumerate(_NON_FINITE_CASES):
        for given in (text, None):
            expected = _rows(_ref_absorb(_normalised(spans), given))
            assert _same(_rows(sp.absorb_overlapping_spans(_copy(spans), given)), expected), number
            expected = _rows(_ref_dedupe(_normalised(spans), high, given))
            assert _same(_rows(sp.deduplicate_spans(_copy(spans), high, given)), expected), number


def test_non_finite_scores_are_clamped() -> None:
    """NaN and -inf read as 0.0, +inf as 1.0; no span is dropped for its score."""
    high = {"DATE", "LOCATION"}
    spans = [
        DetectedSpan(1, 6, "LOCATION", 0.5),
        DetectedSpan(1, 6, "DATE", math.nan),
        DetectedSpan(1, 6, "DATE_OF_BIRTH", 0.5),
        DetectedSpan(1, 6, "LOCATION", 0.5),
        DetectedSpan(1, 6, "DATE", 1.0),
        DetectedSpan(0, 2, "DATE", 0.5),
    ]
    first = _rows(sp.deduplicate_spans(_copy(spans), high, "abcdefg"))
    again = _rows(sp.deduplicate_spans(_copy(spans), high, "abcdefg"))
    assert _same(first, again)
    assert _same(first, _rows(sp.deduplicate_spans(_normalised(spans), high, "abcdefg")))
    assert all(math.isfinite(row[3]) for row in first)

    lone = [
        DetectedSpan(0, 3, "LOCATION", math.nan),
        DetectedSpan(4, 7, "DATE", math.inf),
        DetectedSpan(8, 11, "PERSON_NAME", -math.inf),
    ]
    rows = _rows(sp.deduplicate_spans(lone, set(), "abc def ghi"))
    assert [(r[0], r[1], r[3]) for r in rows] == [(0, 3, 0.0), (4, 7, 1.0), (8, 11, 0.0)]
    rows = _rows(sp.absorb_overlapping_spans(lone, "abc def ghi"))
    assert [(r[0], r[1], r[3]) for r in rows] == [(0, 3, 0.0), (4, 7, 1.0), (8, 11, 0.0)]


# ---------------------------------------------------------------------------
# Properties over the corpus
# ---------------------------------------------------------------------------


def _alnum_covered(text: str, spans: List[DetectedSpan]) -> set[int]:
    limit = len(text)
    return {
        i
        for s in spans
        for i in range(min(max(s.start, 0), limit), min(max(s.end, 0), limit))
        if text[i].isalnum()
    }


def test_dedupe_output_is_disjoint_keeps_coverage_and_is_stable() -> None:
    for number, (text, spans, high) in enumerate(_CASES):
        out = sp.deduplicate_spans(_copy(spans), high, text)
        for left, right in zip(out, out[1:]):
            assert left.end <= right.start, (number, _rows(out))
        assert _alnum_covered(text, spans) <= _alnum_covered(text, out), number
        assert _rows(sp.deduplicate_spans(_copy(out), high, text)) == _rows(out), number


def test_absorb_output_keeps_coverage_and_is_stable() -> None:
    for number, (text, spans, _) in enumerate(_CASES):
        out = sp.absorb_overlapping_spans(_copy(spans), text)
        assert _alnum_covered(text, spans) <= _alnum_covered(text, out), number
        assert _rows(sp.absorb_overlapping_spans(_copy(out), text)) == _rows(out), number
