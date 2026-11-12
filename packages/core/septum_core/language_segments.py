"""Split a document into contiguous language segments.

A mixed-language document (a contract with an annex in another language)
is cut at the block boundaries the pipeline already hands to the detector.
Each block is judged with :func:`resolve_language`, adjacent blocks of one
language merge into runs, and a run too short to be trusted inherits its
neighbour's language. The module is pure: language detection is an injected
callable, so core imports no detector and no network library.
"""

from __future__ import annotations

import heapq
import itertools
import logging
from bisect import bisect_right
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .language_resolution import (
    deciding_script_from_counts,
    detection_sample,
    iso_language_code,
    resolve_language,
    script_forced_language,
    script_letter_counts,
)
from .text_utils import normalize_unicode

logger = logging.getLogger(__name__)

# A run of one language shorter than this is not trusted as a language of
# its own: it inherits the language of its longer neighbour. A few lines of
# quoted foreign text are not worth loading another model set. The same size
# bounds how far short blocks are coalesced before a language is detected: a
# piece shorter than this cannot stand as a segment, so it is judged together
# with its short neighbours (a document of many tiny blocks, such as DOCX
# paragraphs or audio segments, would otherwise pay one detector call per
# block), while a block of at least this size is judged alone and a short block
# is never joined to a long one that follows it.
DEFAULT_MIN_RUN_CHARS = 400

# Scripts whose characters each carry far more than a Latin letter does: a
# Han, kana, Hangul or Thai line says in ~120 characters what a Latin one says
# in ~400. Their runs are trusted at that floor, keyed by the script that
# decides the run's language (``language_resolution.deciding_script``).
# Measured on the mixed-language evaluation documents: Han sections of 520-600
# characters made of paragraphs of 19-171 characters were wholly swallowed by a
# 400-character floor and masked with the wrong models. Every other script
# keeps ``DEFAULT_MIN_RUN_CHARS``.
MIN_RUN_CHARS_BY_SCRIPT = {
    "Han": 120,
    "Kana": 120,
    "Hangul": 120,
    "Thai": 120,
}

# A unit in a language other than the dominant one, whose detector probability
# for that language (summed over its region variants) is at least this, is
# trusted at the smaller sizes below. A language the scripts
# alone settle (:func:`language_resolution.script_forced_language`) is certain.
CONFIDENT_UNIT_PROBABILITY = 0.95

# Shortest run that keeps its own label when its language is confident, by the
# script deciding the run, in the unit of ``MIN_RUN_CHARS_BY_SCRIPT``. Measured
# on isolated minority clauses between long dominant paragraphs: a Han clause
# of 120 characters or more kept its own segment and its names leaked 0.06-0.10
# instead of 0.80-0.88, while shorter ones (40 and 80) were absorbed and
# stayed at today's rate. About a third of the full floors, with a quarter of
# the Latin floor (80 against 400) and a third of the dense one (40 against
# 120); below these a run is a clause, not a passage, whatever the detector
# says.
DEFAULT_CONFIDENT_MIN_RUN_CHARS = 80
CONFIDENT_MIN_RUN_CHARS_BY_SCRIPT = {
    "Han": 40,
    "Kana": 40,
    "Hangul": 40,
    "Thai": 40,
}

# A unit with fewer letters than this is not shown to the detector: it takes
# its neighbour's language, as a unit without letters does. A few words are too
# little for a character n-gram detector to tell languages apart, and the unit
# is far below the shortest run a confident unit keeps (80 characters of a
# spaced script, some 60 letters), so only its context can decide it. Measured:
# the smallest unit the detector was asked about in the evaluation documents
# had 31 letters. Without this, one letter between long blocks of a script that
# settles its own language (``"a"`` + 120 Han characters, repeated) cost one
# detector call per 121 characters.
MIN_DETECTION_LETTERS = 20

# Most detector calls one document may make. Past it the document is
# segmented no further and stays in its dominant language, exactly as without
# segmentation. Measured on the evaluation documents: at most 2.5 calls per
# 1 000 characters (151 for 69 000), about 1 ms each, so the budget covers some
# 1.6 million characters of realistic text and bounds the detector work of any
# document at a few seconds of realistic calls; the slowest measured call
# (about 10 ms, on short repetitive text) puts the ceiling at about 40 seconds.
MAX_DETECTOR_CALLS_PER_DOCUMENT = 4_000

CandidateDetector = Callable[[str], Iterable[Tuple[str, float]]]


@dataclass(frozen=True)
class Segment:
    """A half-open character range ``[start, end)`` of the document in one language."""

    start: int
    end: int
    language: str


def _min_run_chars(counts: Dict[str, int], letters: int, confident: bool = False) -> int:
    """Shortest trusted length for text with these script letter counts.

    ``confident`` selects the smaller sizes for a run in a language the
    detector (or the scripts) settled with high confidence.
    """
    script = deciding_script_from_counts(counts, letters)
    if confident:
        return CONFIDENT_MIN_RUN_CHARS_BY_SCRIPT.get(script, DEFAULT_CONFIDENT_MIN_RUN_CHARS)
    return MIN_RUN_CHARS_BY_SCRIPT.get(script, DEFAULT_MIN_RUN_CHARS)


def _is_confident(language: str, candidates: Sequence[Tuple[str, float]]) -> bool:
    """Whether the detector gives ``language``, summed over its region variants, enough probability.

    The probabilities share one unit, so a language at the threshold (above
    one half) is also the detector's top one.
    """
    total = sum(
        probability for code, probability in candidates if iso_language_code(code) == language
    )
    return total >= CONFIDENT_UNIT_PROBABILITY


def _add_counts(into: Dict[str, int], other: Dict[str, int]) -> None:
    for script, count in other.items():
        into[script] += count


_Unit = Tuple[int, int, str, Dict[str, int], int]


def _units(text: str, boundaries: Sequence[int]) -> List[_Unit]:
    """Blocks cut at ``boundaries``, with consecutive short blocks coalesced.

    Returns ``(start, end, sample, script counts, letters)`` per unit, where
    ``sample`` is the unit's :func:`detection_sample` and the counts are its
    :func:`script_letter_counts`. A block is short below the
    :data:`MIN_RUN_CHARS_BY_SCRIPT` size of its own script (else
    :data:`DEFAULT_MIN_RUN_CHARS`); consecutive short blocks join until the
    joined text reaches the size of the script its letters mostly use. A long
    block is its own unit and also ends the unit before it, so a language
    change at a long block's edge is never blurred.

    Boundaries follow the detector's convention: offsets strictly inside
    ``text``; duplicates, unsorted values and offsets at or outside the ends
    are ignored.
    """
    length = len(text)
    cuts = sorted({b for b in boundaries if 0 < b < length})
    edges = [0, *cuts, length]
    units: List[_Unit] = []
    pending: List[_Unit] = []
    pending_counts: Dict[str, int] = {}
    pending_letters = 0

    def flush() -> None:
        nonlocal pending, pending_counts, pending_letters
        if not pending:
            return
        if len(pending) == 1:
            units.append(pending[0])
        else:
            start, end = pending[0][0], pending[-1][1]
            sample = detection_sample(text[start:end])
            units.append((start, end, sample, *script_letter_counts(sample)))
        pending, pending_counts, pending_letters = [], {}, 0

    for start, end in zip(edges, edges[1:]):
        sample = detection_sample(text[start:end])
        counts, letters = script_letter_counts(sample)
        block = (start, end, sample, counts, letters)
        if end - start >= _min_run_chars(counts, letters):
            flush()
            units.append(block)
            continue
        pending.append(block)
        if pending_counts:
            _add_counts(pending_counts, counts)
        else:
            pending_counts = dict(counts)
        pending_letters += letters
        if end - pending[0][0] >= _min_run_chars(pending_counts, pending_letters):
            flush()
    flush()
    return units


class _Run:
    """A run of units in one language, with the summed script letter counts of its units."""

    __slots__ = (
        "start", "end", "language", "counts", "letters", "confident", "alive", "version", "prev", "next"
    )

    def __init__(
        self,
        start: int,
        end: int,
        language: str,
        counts: Dict[str, int],
        letters: int,
        confident: bool = False,
    ) -> None:
        self.start = start
        self.end = end
        self.language = language
        self.counts = counts
        self.letters = letters
        self.confident = confident
        self.alive = True
        self.version = 0
        self.prev: Optional[_Run] = None
        self.next: Optional[_Run] = None

    @property
    def length(self) -> int:
        return self.end - self.start

    def floor(self, dominant: str) -> int:
        """Shortest trusted length, by the script that decides the run's own text.

        The smaller size applies to a confident run in a language other than
        ``dominant``.
        """
        return _min_run_chars(self.counts, self.letters, self.confident and self.language != dominant)


def _absorb_short_runs(runs: List[_Run], dominant: str) -> List[_Run]:
    """Merge runs shorter than their script's minimum into their longer neighbour.

    A run's minimum is :data:`MIN_RUN_CHARS_BY_SCRIPT` (else
    :data:`DEFAULT_MIN_RUN_CHARS`) for the script deciding the run's own text,
    read from the summed letter counts of its units, so a merge costs no text
    scan. The shortest short run goes first (leftmost on a tie); it joins the
    longer adjacent run (the previous one on a tie), and the merged run's
    minimum is judged again. A lone short run takes the dominant language.
    A heap over a linked list keeps this O(R log R) in the run count.
    """
    for left, right in zip(runs, runs[1:]):
        left.next, right.prev = right, left
    heap: List[Tuple[int, int, int, int, _Run]] = []
    sequence = itertools.count()

    def push(run: _Run) -> None:
        if run.length < run.floor(dominant):
            heapq.heappush(heap, (run.length, run.start, run.version, next(sequence), run))

    for run in runs:
        push(run)
    alive = len(runs)
    while heap:
        _, _, version, _, run = heapq.heappop(heap)
        if not run.alive or run.version != version:
            continue
        if alive == 1:
            run.language = dominant
            break
        prev_len = run.prev.length if run.prev else -1
        next_len = run.next.length if run.next else -1
        target = run.prev if prev_len >= next_len else run.next
        low, high = (target, run) if target is run.prev else (run, target)
        low.end = high.end
        low.language = target.language
        low.confident = target.confident
        for script, count in high.counts.items():
            low.counts[script] += count
        low.letters += high.letters
        low.version += 1
        high.alive = False
        low.next = high.next
        if high.next:
            high.next.prev = low
        alive -= 1
        push(low)
    return [run for run in runs if run.alive]


def segment_languages(
    text: str,
    boundaries: Sequence[int],
    *,
    dominant_language: str,
    detect_candidates: CandidateDetector,
) -> Tuple[Segment, ...]:
    """Cut ``text`` into contiguous language segments covering ``[0, len(text))``.

    ``boundaries`` are the block offsets passed to ``Detector.detect``.
    Each unit (a block, or consecutive short blocks coalesced up to the
    minimum run size) is resolved from at most its :func:`detection_sample`,
    with ``dominant_language`` as context and default; a unit whose scripts
    allow only one language (:func:`script_forced_language`) never reaches
    the detector. A unit without letters, or with fewer than
    :data:`MIN_DETECTION_LETTERS`, takes its previous neighbour's language (the
    next one for a leading unit) without asking the detector. A document that
    would need more than :data:`MAX_DETECTOR_CALLS_PER_DOCUMENT` detector calls
    is one ``Segment(0, len(text), dominant)``. Adjacent units of
    one language merge; runs shorter than their script's minimum
    (:data:`MIN_RUN_CHARS_BY_SCRIPT`, else :data:`DEFAULT_MIN_RUN_CHARS`) then
    inherit from their longer neighbour. A run in a language other than the
    dominant one whose units were all confident (see
    :data:`CONFIDENT_UNIT_PROBABILITY`) is trusted down to the smaller
    :data:`CONFIDENT_MIN_RUN_CHARS_BY_SCRIPT` sizes. When every segment resolves to the dominant
    language the result is exactly one ``Segment(0, len(text), dominant)``.
    Empty text yields no segments. Raises ``ValueError`` for a
    ``dominant_language`` that is not an ISO 639 code.
    """
    dominant = iso_language_code(dominant_language)
    if dominant is None:
        raise ValueError("dominant_language must be an ISO 639 language code")
    if not text:
        return ()

    resolved: List[Tuple[int, int, Optional[str], Dict[str, int], int, Optional[bool]]] = []
    calls = 0
    for start, end, sample, counts, letters in _units(text, boundaries):
        confident: Optional[bool] = None
        # The scripts alone may leave one possible answer; then the detector
        # cannot change it and is not asked.
        language = script_forced_language(counts, letters)
        if language is not None:
            confident = True
        elif letters >= MIN_DETECTION_LETTERS:
            if calls == MAX_DETECTOR_CALLS_PER_DOCUMENT:
                logger.warning(
                    "Document needs more than %d language detector calls; masking it in its dominant language",
                    MAX_DETECTOR_CALLS_PER_DOCUMENT,
                )
                return (Segment(0, len(text), dominant),)
            calls += 1
            candidates = list(detect_candidates(sample))
            language = resolve_language(
                sample,
                candidates,
                context_language=dominant,
                default=dominant,
                script_counts=(counts, letters),
            )
            confident = _is_confident(language, candidates)
        resolved.append((start, end, language, counts, letters, confident))

    known = [lang for _, _, lang, _, _, _ in resolved if lang is not None]
    previous = known[0] if known else dominant
    runs: List[_Run] = []
    for start, end, language, counts, letters, confident in resolved:
        language = language or previous
        previous = language
        if runs and runs[-1].language == language:
            tail = runs[-1]
            tail.end = end
            for script, count in counts.items():
                tail.counts[script] += count
            tail.letters += letters
            tail.confident = tail.confident and confident is not False
        else:
            runs.append(_Run(start, end, language, dict(counts), letters, confident is not False))

    merged: List[List] = []
    for run in _absorb_short_runs(runs, dominant):
        if merged and merged[-1][2] == run.language:
            merged[-1][1] = run.end
        else:
            merged.append([run.start, run.end, run.language])
    return tuple(Segment(start, end, language) for start, end, language in merged)


def validate_segments(segments: Sequence[Segment], length: int) -> Tuple[Segment, ...]:
    """``segments`` as canonical-language segments, checked to tile ``[0, length)``.

    The detector trusts nothing the caller computed: a gap, an overlap, an
    empty range or a language that is not an ISO 639 code would silently
    mask part of the text with the wrong rules, so each raises ``ValueError``.
    """
    checked: List[Segment] = []
    expected = 0
    for segment in segments:
        language = iso_language_code(segment.language)
        if language is None:
            raise ValueError("segment language must be an ISO 639 language code")
        if segment.start != expected or segment.end <= segment.start:
            raise ValueError("segments must be contiguous, non-empty and start at 0")
        expected = segment.end
        checked.append(Segment(segment.start, segment.end, language))
    if expected != length:
        raise ValueError("segments must cover the whole text")
    return tuple(checked)


# Characters read on each side of a cut to tell whether normalization could
# join them. A combining sequence in stream-safe text is at most 30 marks.
_NFC_CONTEXT = 64


def _is_stable_cut(text: str, cut: int) -> bool:
    """Whether normalizing ``text`` in two pieces at ``cut`` gives what normalizing it whole does."""
    low = max(cut - _NFC_CONTEXT, 0)
    high = min(cut + _NFC_CONTEXT, len(text))
    return normalize_unicode(text[low:cut]) + normalize_unicode(text[cut:high]) == normalize_unicode(
        text[low:high]
    )


def rebase_segments(segments: Sequence[Segment], text: str) -> Optional[Tuple[Segment, ...]]:
    """``segments`` of ``text`` moved onto its Unicode-normalized form, or ``None`` if that cannot be done reliably.

    Normalization can change the length. Each cut between segments is moved
    back, by at most :data:`_NFC_CONTEXT` characters, to a place where
    normalizing the text in two pieces equals normalizing it whole (a cut
    between a base letter and its combining mark is not such a place). A cut
    with no such place in reach (inside a very long run of marks) is dropped:
    its segment merges into the next one. Offsets are the cumulative
    normalized lengths of the pieces; a segment the move empties is dropped.
    Each cut costs a bounded amount of work, so the whole is linear in the
    text.

    The check around a cut is local and assumes stream-safe text (no more than
    about 30 marks in a row). Text that is not (a base letter followed by
    hundreds of marks and then one that composes with it) can pass the local
    check and still be cut where the pieces' lengths do not add up to the
    whole's. The pieces are therefore summed in full, and when they do not
    equal the normalized length the result is ``None``: the caller must not
    trust segments for that text. A text that normalization leaves unchanged
    returns the segments as they are.
    """
    normalized = normalize_unicode(text)
    if normalized == text:
        return tuple(segments)
    rebased: List[Segment] = []
    old_start = 0
    new_start = 0
    for index, segment in enumerate(segments):
        if index == len(segments) - 1:
            old_end = len(text)
            piece = len(normalized) if old_start == 0 else len(normalize_unicode(text[old_start:]))
        else:
            old_end = segment.end
            floor = max(old_start, old_end - _NFC_CONTEXT)
            while old_end > floor and not _is_stable_cut(text, old_end):
                old_end -= 1
            if not _is_stable_cut(text, old_end):
                continue
            piece = len(normalize_unicode(text[old_start:old_end]))
        new_end = new_start + piece
        if new_end > new_start:
            rebased.append(Segment(new_start, new_end, segment.language))
        old_start, new_start = old_end, new_end
    if new_start != len(normalized) or not rebased:
        return None
    return tuple(rebased)


def clip_segments(segments: Sequence[Segment], start: int, end: int) -> Tuple[Segment, ...]:
    """The part of ``segments`` inside ``[start, end)``, shifted so ``start`` becomes 0."""
    return tuple(
        Segment(max(s.start, start) - start, min(s.end, end) - start, s.language)
        for s in segments
        if s.start < end and s.end > start
    )


class SegmentLookup:
    """Language of the segment containing a character offset, in O(log n)."""

    def __init__(self, segments: Sequence[Segment]) -> None:
        if not segments:
            raise ValueError("at least one segment is required")
        self._starts = [s.start for s in segments]
        self._languages = [s.language for s in segments]

    def language_at(self, position: int) -> str:
        """Language of the segment holding ``position``; offsets outside clamp to the nearest segment."""
        return self._languages[max(bisect_right(self._starts, position) - 1, 0)]


__all__ = [
    "CandidateDetector",
    "Segment",
    "SegmentLookup",
    "clip_segments",
    "rebase_segments",
    "segment_languages",
    "validate_segments",
]
