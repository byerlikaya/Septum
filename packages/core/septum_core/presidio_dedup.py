"""Linearithmic replacement for Presidio's ``EntityRecognizer.remove_duplicates``.

Presidio before 2.2.366 filters results by checking each one against every
result kept so far (a list ``in`` test plus a ``contained_in`` loop), which is
quadratic in the number of matches. ``PatternRecognizer`` runs it once per
recognizer and ``AnalyzerEngine`` once more over all results, so digit-dense
input (an IPv4 address or a 12-digit ID every few characters) cost seconds per
40K characters. The api cannot move to 2.2.366 yet (paddlex pins PyYAML), so
the replacement is installed on every version and returns what 2.2.366 does:
the distinct results sorted by descending score, then start, then descending
length, then entity type; zero scores dropped; a result dropped when an
already kept result of the same entity type contains its span.
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from typing import Dict, Iterable, List

from presidio_analyzer import EntityRecognizer, RecognizerResult

# Presidio's original, captured before install(); the oracle for the equivalence tests.
PRESIDIO_REMOVE_DUPLICATES = EntityRecognizer.remove_duplicates


class _KeptSpans:
    """Kept spans of one entity type, answering "is this span contained?".

    A Fenwick tree over the distinct start offsets holds the largest kept end
    for every prefix of starts. A span is contained in a kept one exactly when
    some kept span starting at or before it ends at or after it.
    """

    def __init__(self, starts: Iterable[int]) -> None:
        self._starts = sorted(set(starts))
        self._max_end = [-math.inf] * (len(self._starts) + 1)

    def contains(self, start: int, end: int) -> bool:
        i = bisect_right(self._starts, start)
        best = -math.inf
        while i > 0:
            best = max(best, self._max_end[i])
            i -= i & -i
        return best >= end

    def add(self, start: int, end: int) -> None:
        i = bisect_left(self._starts, start) + 1
        while i < len(self._max_end):
            self._max_end[i] = max(self._max_end[i], end)
            i += i & -i


def remove_duplicates(results: List[RecognizerResult]) -> List[RecognizerResult]:
    """Same output as Presidio 2.2.366's ``remove_duplicates`` in O(n log n).

    Presidio's extra ``result not in filtered_results`` test is subsumed by
    the containment test: an equal result has the same type and span.
    """
    ordered = sorted(
        set(results),
        key=lambda x: (-x.score, x.start, -(x.end - x.start), str(x.entity_type)),
    )
    starts_by_type: Dict[str, List[int]] = {}
    for result in ordered:
        starts_by_type.setdefault(result.entity_type, []).append(result.start)
    kept_by_type = {entity: _KeptSpans(starts) for entity, starts in starts_by_type.items()}

    filtered: List[RecognizerResult] = []
    for result in ordered:
        if result.score == 0:
            continue
        kept = kept_by_type[result.entity_type]
        if kept.contains(result.start, result.end):
            continue
        kept.add(result.start, result.end)
        filtered.append(result)
    return filtered


def install() -> None:
    """Route every Presidio ``remove_duplicates`` call through the fast version.

    ``PatternRecognizer`` and ``AnalyzerEngine`` call it as
    ``EntityRecognizer.remove_duplicates(...)``, so replacing the class
    attribute covers both. Idempotent.
    """
    EntityRecognizer.remove_duplicates = staticmethod(remove_duplicates)
