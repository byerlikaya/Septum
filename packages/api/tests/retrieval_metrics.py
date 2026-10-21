"""Deterministic, stdlib-only metric helpers for the retrieval benchmark.

Leak definitions. A leak always means that raw PII text survives in the
sanitized output; placeholders such as ``[PERSON_NAME_1]`` never count.

Literal leak: the value, casefolded, occurs verbatim in the sanitized text
of a chunk that overlaps it.

Fragment leak: a recoverable part of the value survives at the value's own
position. The position is the sanitized span that replaces the value's raw
characters, found by aligning the chunk's raw input to its sanitized output
(``alignment`` and ``sanitized_span``), so a fragment elsewhere in the chunk
is not attributed to the value.

- identifier (email, phone, IBAN): a contiguous run of the value's letters and
  digits (separators removed, casefolded) of length max(6, ceil(n / 2)), n
  being the compact length, survives in the region. Values shorter than 6
  compact characters are not fragment-scorable.
- name, space-delimited scripts: a whole token (at least 3 characters) survives
  as a word in the region. Tokens are Unicode word runs.
- name, unspaced scripts (Han, kana, Hangul): the name is one token with no word
  boundaries; a contiguous run of max(2, ceil(n / 2)) characters survives as a
  substring of the region.

A value with no scorable token is not fragment-scorable; its literal check
still runs.
"""

from __future__ import annotations

import difflib
import math
import random
import re
from typing import Optional, Sequence

Span = tuple[int, int]
Opcode = tuple[str, int, int, int, int]

MIN_COVERAGE = 0.5
MIN_ANCHOR = 3
MIN_FRAGMENT_RUN = 6
MIN_SPACED_TOKEN = 3
MIN_UNSPACED_TOKEN = 2

_PLACEHOLDER = re.compile(r"\[[A-Z][A-Z0-9_]*_\d+\]")
_NON_ALNUM = re.compile(r"[\W_]+", re.UNICODE)
_WORD = re.compile(r"\w+", re.UNICODE)
_UNSPACED_SCRIPT_RANGES = (
    (0x2E80, 0x2FDF),
    (0x3040, 0x30FF),
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xAC00, 0xD7AF),
    (0xF900, 0xFAFF),
    (0x20000, 0x2FA1F),
)


def covered_fraction(chunk: Span, gold: Span) -> float:
    """Fraction of the gold span covered by the chunk span (0.0 to 1.0)."""
    gold_len = gold[1] - gold[0]
    if gold_len <= 0:
        return 0.0
    overlap = min(chunk[1], gold[1]) - max(chunk[0], gold[0])
    return max(0, overlap) / gold_len


def rank_gains(chunk_spans: Sequence[Span], gold_spans: Sequence[Span]) -> list[float]:
    """Per-rank graded gain with each gold span credited once.

    A gold span is credited at the first rank whose chunk covers at least
    50% of it; the credit equals the covered fraction. Later chunks that
    cover the same gold span earn nothing.
    """
    gains = [0.0] * len(chunk_spans)
    for gold in gold_spans:
        for rank, chunk in enumerate(chunk_spans):
            fraction = covered_fraction(chunk, gold)
            if fraction >= MIN_COVERAGE:
                gains[rank] += fraction
                break
    return gains


def recall_at_k(
    chunk_spans: Sequence[Span], gold_spans: Sequence[Span], k: int
) -> float:
    """Fraction of gold spans satisfied within the top k chunks."""
    if not gold_spans:
        return 0.0
    satisfied = sum(
        1
        for gold in gold_spans
        if any(covered_fraction(c, gold) >= MIN_COVERAGE for c in chunk_spans[:k])
    )
    return satisfied / len(gold_spans)


def mrr(chunk_spans: Sequence[Span], gold_spans: Sequence[Span]) -> float:
    """Reciprocal rank of the first relevant chunk, 0.0 if none."""
    for rank, gain in enumerate(rank_gains(chunk_spans, gold_spans), start=1):
        if gain > 0:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(
    chunk_spans: Sequence[Span], gold_spans: Sequence[Span], k: int
) -> float:
    """nDCG@k with graded gains and log2(rank + 1) discount.

    The ideal DCG places every gold span at gain 1.0 in the top positions.
    """
    if not gold_spans:
        return 0.0
    gains = rank_gains(chunk_spans[:k], gold_spans)
    dcg = sum(g / math.log2(i + 2) for i, g in enumerate(gains))
    ideal = sum(1.0 / math.log2(i + 2) for i in range(min(len(gold_spans), k)))
    return dcg / ideal if ideal else 0.0


def mean(values: Sequence[float]) -> float:
    """Arithmetic mean, 0.0 for an empty sequence."""
    return sum(values) / len(values) if values else 0.0


def percentile(values: Sequence[float], p: float) -> float:
    """Percentile with linear interpolation between closest ranks.

    Uses rank ``(n - 1) * p / 100`` over the sorted values (the same method
    as numpy's default). ``p`` is in [0, 100].
    """
    if not values:
        raise ValueError("percentile of empty sequence")
    if not 0 <= p <= 100:
        raise ValueError("p must be within [0, 100]")
    ordered = sorted(values)
    position = (len(ordered) - 1) * p / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def bootstrap_ci(
    values: Sequence[float],
    seed: int,
    n_resamples: int = 1000,
    alpha: float = 0.05,
) -> tuple[float, float]:
    """Percentile bootstrap confidence interval of the mean.

    Deterministic for a given seed. Returns the (alpha/2, 1 - alpha/2)
    percentiles of the resampled means, interpolated as in ``percentile``.
    """
    if not values:
        raise ValueError("bootstrap of empty sequence")
    rng = random.Random(seed)
    n = len(values)
    means = [
        sum(values[rng.randrange(n)] for _ in range(n)) / n
        for _ in range(n_resamples)
    ]
    return (
        percentile(means, 100 * alpha / 2),
        percentile(means, 100 * (1 - alpha / 2)),
    )


def _strip_placeholders(text: str) -> str:
    return _PLACEHOLDER.sub(" ", text)


def _is_unspaced(token: str) -> bool:
    """Whether the token holds a character of a script written without spaces."""
    return any(lo <= ord(ch) <= hi for ch in token for lo, hi in _UNSPACED_SCRIPT_RANGES)


def _compact(text: str) -> str:
    return _NON_ALNUM.sub("", text).casefold()


def name_tokens(value: str) -> list[str]:
    """Casefolded name tokens that the fragment check can score.

    Tokens are maximal ``\\w`` runs. A run with an unspaced-script character
    is kept whole and needs at least 2 characters; any other run needs at
    least 3.
    """
    tokens = []
    for run in _WORD.findall(value):
        minimum = MIN_UNSPACED_TOKEN if _is_unspaced(run) else MIN_SPACED_TOKEN
        if len(run) >= minimum:
            tokens.append(run.casefold())
    return tokens


def fragment_scorable(value: str, type_class: str) -> bool:
    """Whether the fragment check can ever report a leak for this value."""
    if type_class == "identifier":
        return len(_compact(value)) >= MIN_FRAGMENT_RUN
    if type_class == "name":
        return bool(name_tokens(value))
    raise ValueError(f"unknown type class: {type_class}")


def has_literal_leak(value: str, sanitized_text: str) -> bool:
    """Whether the value occurs verbatim (casefolded) in the sanitized text."""
    needle = value.casefold()
    return bool(needle) and needle in _strip_placeholders(sanitized_text).casefold()


def has_fragment_leak(value: str, type_class: str, region: str) -> bool:
    """Whether a recoverable part of the value survives in its sanitized region.

    ``region`` is the sanitized text aligned to the value (see
    ``sanitized_span``). Returns False for values that are not
    fragment-scorable.
    """
    if not fragment_scorable(value, type_class):
        return False
    if type_class == "identifier":
        compact = _compact(value)
        run = max(MIN_FRAGMENT_RUN, math.ceil(len(compact) / 2))
        survived = _compact(_strip_placeholders(region))
        return any(compact[i : i + run] in survived for i in range(len(compact) - run + 1))
    text = _strip_placeholders(region).casefold()
    words = set(_WORD.findall(text))
    for token in name_tokens(value):
        if _is_unspaced(token):
            run = max(MIN_UNSPACED_TOKEN, math.ceil(len(token) / 2))
            if any(token[i : i + run] in text for i in range(len(token) - run + 1)):
                return True
        elif token in words:
            return True
    return False


def alignment(raw: str, sanitized: str) -> list[Opcode]:
    """Character opcodes (difflib) aligning a chunk's raw input to its sanitized output.

    Equal blocks shorter than ``MIN_ANCHOR`` characters are not trusted as
    anchors (a single letter matches anywhere, e.g. inside a placeholder);
    they are merged into the changed run around them, so the run keeps its
    true sanitized position.
    """
    opcodes = difflib.SequenceMatcher(None, raw, sanitized, autojunk=False).get_opcodes()
    merged: list[Opcode] = []
    run: Optional[list[int]] = None
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal" and i2 - i1 >= MIN_ANCHOR:
            if run is not None:
                merged.append(_close_run(run))
                run = None
            merged.append((tag, i1, i2, j1, j2))
        elif run is None:
            run = [i1, i2, j1, j2]
        else:
            run[1], run[3] = i2, j2
    if run is not None:
        merged.append(_close_run(run))
    return merged


def _close_run(run: list[int]) -> Opcode:
    i1, i2, j1, j2 = run
    return ("replace" if i2 > i1 else "insert", i1, i2, j1, j2)


def sanitized_span(opcodes: Sequence[Opcode], start: int, end: int) -> Span:
    """Sanitized char span that replaces the raw characters ``[start, end)``.

    Unchanged characters map exactly. A replaced or deleted block counts in
    full when it touches the range; an inserted block counts only when it
    lies strictly inside the range. Returns ``(0, 0)`` when nothing maps.
    """
    lo: Optional[int] = None
    hi: Optional[int] = None
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            a, b = max(i1, start), min(i2, end)
            if a >= b:
                continue
            s, t = j1 + (a - i1), j1 + (b - i1)
        elif tag == "insert":
            if not start < i1 < end:
                continue
            s, t = j1, j2
        elif i2 > start and i1 < end:
            s, t = j1, j2
        else:
            continue
        lo = s if lo is None else min(lo, s)
        hi = t if hi is None else max(hi, t)
    return (0, 0) if lo is None else (lo, hi)


def classify_boundary(value_span: Optional[Span], chunk_spans: Sequence[Span]) -> str:
    """Classify a value as ``crossing``, ``control`` or ``unknown``.

    ``crossing``: some chunk start or end falls strictly inside the value
    span. ``unknown``: the span is ``None``.
    """
    if value_span is None:
        return "unknown"
    start, end = value_span
    for chunk_start, chunk_end in chunk_spans:
        if start < chunk_start < end or start < chunk_end < end:
            return "crossing"
    return "control"
