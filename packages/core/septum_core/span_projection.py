"""Projection of whole-document PII spans onto chunk ranges.

Detection runs once over the whole document and ``Detector.apply`` assigns
placeholders once, in document order. This module then cuts the document into
chunks without re-detecting: a value split by a chunk boundary is masked in
every chunk it touches, because the clipped fragment is replaced by the span's
full placeholder. The same value therefore carries the same placeholder in
every chunk.

Raw text is only ever sliced here; nothing is logged, cached or serialized.
"""

from __future__ import annotations

import operator
from bisect import bisect_right
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, SupportsIndex, Tuple

from .anonymization_map import _TEXT_TOKEN, AnonymizationMap
from .spans import DetectionResult, ResolvedSpan, entity_range


@dataclass(frozen=True)
class ProjectedSpan:
    """A document span as seen from one chunk.

    ``start``/``end`` are offsets in the raw chunk slice. ``clipped`` is True
    when the chunk cuts the span. ``core`` is the entity part inside the chunk;
    ``None`` means the whole range is the entity only while ``core_outside`` is
    False. ``core_outside`` is True when the span has a core and none of it
    lies in this chunk, so the visible fragment is only a token tail.

    Provenance and display only: a clipped span is a fragment, never the
    entity. Never register it, or its text, in an anonymization map.
    """

    start: int
    end: int
    entity_type: str
    placeholder: str
    score: float
    core: Optional[Tuple[int, int]] = None
    clipped: bool = False
    core_outside: bool = False


@dataclass(frozen=True)
class ProjectedChunk:
    """One chunk of a document with its PII masked.

    ``start``/``end`` locate the chunk in ``DetectionResult.text``.
    ``masked_text`` is that slice with every overlapping span replaced by its
    placeholder and every blocklisted residual mention redacted; it is the only
    safe output. ``spans`` are the overlapping spans (see ``ProjectedSpan``),
    in offsets of the raw slice (``text[start:end]``), not of ``masked_text``.
    They are provenance/display only, do not include the blocklist edits, and
    must never be registered in an anonymization map. Never rebuild text from
    ``spans`` plus the raw slice: that would expose residual mentions.
    """

    start: int
    end: int
    masked_text: str
    spans: Tuple[ProjectedSpan, ...] = ()


@dataclass(frozen=True)
class _Edit:
    """A raw range ``[start, end)`` shown as ``text`` in every chunk touching it."""

    start: int
    end: int
    text: str
    span: Optional[ResolvedSpan] = None


def _index(value: SupportsIndex, what: str) -> int:
    """``value`` as an ``int``; ``TypeError`` for floats, bools and non-numbers."""
    if isinstance(value, bool):
        raise TypeError(f"{what} must be an integer, not bool")
    return operator.index(value)


def _validated_spans(text: str, spans: Sequence[ResolvedSpan]) -> List[ResolvedSpan]:
    """Spans sorted by start; ``ValueError`` on bad bounds or overlap."""
    ordered = sorted(spans, key=lambda s: _index(s.start, "span start"))
    previous_end = 0
    for span in ordered:
        start, end = _index(span.start, "span start"), _index(span.end, "span end")
        if not 0 <= start < end <= len(text):
            raise ValueError(
                f"span [{start}, {end}) is outside the text of length {len(text)}"
            )
        if start < previous_end:
            raise ValueError(f"span [{start}, {end}) overlaps a previous span")
        previous_end = end
    return ordered


def _checked_against_detection(
    detection: DetectionResult, resolved_spans: Sequence[ResolvedSpan]
) -> List[ResolvedSpan]:
    """Validated spans that are exactly what ``Detector.apply`` kept for ``detection``.

    ``apply`` skips a span that starts inside an earlier kept span and one
    whose text is whitespace-only; every other detection span must have a
    resolved span with the same ``(start, end)``, and no resolved span may be
    unaccounted for. ``ValueError`` otherwise: a misaligned span would leave
    the real value unmasked.
    """
    text = detection.text
    ordered = _validated_spans(text, resolved_spans)
    expected: List[Tuple[int, int]] = []
    last_end = 0
    for span in sorted(detection.spans, key=lambda s: s.start):
        if span.start < last_end:
            continue
        last_end = span.end
        if text[span.start : span.end].strip():
            expected.append((span.start, span.end))
    if [(s.start, s.end) for s in ordered] != expected:
        raise ValueError("resolved_spans do not match the spans detected in the text")
    return ordered


def _validated_ranges(
    text: str, ranges: Sequence[Tuple[int, int]]
) -> List[Tuple[int, int]]:
    """``ranges`` as ints; ``ValueError`` unless ``0 <= start < end <= len(text)``."""
    checked = [
        (_index(start, "range start"), _index(end, "range end")) for start, end in ranges
    ]
    for start, end in checked:
        if not 0 <= start < end <= len(text):
            raise ValueError(
                f"chunk range [{start}, {end}) is invalid for text of length {len(text)}"
            )
    return checked


def _edits(
    detection: DetectionResult, ordered: List[ResolvedSpan], anon_map: AnonymizationMap
) -> List[_Edit]:
    """Every replacement of the whole document, sorted and disjoint.

    The spans, plus each token run outside a span that ``apply_blocklist``
    would redact (a residual mention the detector missed but whose token is
    known). Runs are found once over the whole document, so a chunk edge that
    cuts a run still shows the run's replacement, never a raw fragment. A run
    with only some words known ("Smith-Jones") is shown as the whole
    redacted run.
    """
    text, language = detection.text, detection.language
    edits = [_Edit(s.start, s.end, s.placeholder, s) for s in ordered]
    cache: Dict[str, str] = {}
    cursor = 0
    gaps = [(s.start, s.end) for s in ordered] + [(len(text), len(text))]
    for gap_end, next_cursor in gaps:
        for match in _TEXT_TOKEN.finditer(text, cursor, gap_end):
            run = match.group(0)
            if run.startswith("["):
                continue
            replacement = cache.get(run)
            if replacement is None:
                replacement = cache[run] = anon_map.apply_blocklist(run, language)
            if replacement != run:
                edits.append(_Edit(match.start(), match.end(), replacement))
        cursor = next_cursor
    edits.sort(key=lambda e: e.start)
    return edits


def protected_ranges(
    detection: DetectionResult,
    resolved_spans: Sequence[ResolvedSpan],
    anon_map: AnonymizationMap,
) -> List[Tuple[int, int]]:
    """Merged, sorted ``(start, end)`` ranges that are replaced in every chunk.

    Covers the spans and the blocklisted token runs outside them; validates
    ``resolved_spans`` against ``detection`` as ``project`` does. A chunker may
    prefer boundaries outside every range; ``project`` stays correct for any
    boundary (a cut range shows its whole replacement), so this is an
    optimisation, not a safeguard.
    """
    ordered = _checked_against_detection(detection, resolved_spans)
    merged: List[Tuple[int, int]] = []
    for edit in _edits(detection, ordered, anon_map):
        if merged and edit.start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], edit.end))
        else:
            merged.append((edit.start, edit.end))
    return merged


def _projected(
    span: ResolvedSpan, text: str, chunk_start: int, chunk_end: int
) -> ProjectedSpan:
    """``span`` as seen from the chunk, in offsets of the raw chunk slice."""
    core: Optional[Tuple[int, int]] = None
    core_outside = False
    if span.core is not None:
        core_start, core_end = entity_range(span.start, span.end, span.core, text)
        core_start, core_end = max(core_start, chunk_start), min(core_end, chunk_end)
        if core_start < core_end:
            core = (core_start - chunk_start, core_end - chunk_start)
        else:
            core_outside = True
    return ProjectedSpan(
        start=max(span.start, chunk_start) - chunk_start,
        end=min(span.end, chunk_end) - chunk_start,
        entity_type=span.entity_type,
        placeholder=span.placeholder,
        score=span.score,
        core=core,
        clipped=span.start < chunk_start or span.end > chunk_end,
        core_outside=core_outside,
    )


def project(
    detection: DetectionResult,
    resolved_spans: Sequence[ResolvedSpan],
    ranges: Sequence[Tuple[int, int]],
    anon_map: AnonymizationMap,
) -> List[ProjectedChunk]:
    """Mask each ``(start, end)`` slice of ``detection.text`` using document spans.

    ``resolved_spans`` and ``anon_map`` come from one ``Detector.apply`` over
    ``detection``; text and language are read from it so they cannot be out of
    step with the span offsets. Ranges may overlap and may cut through spans: a
    cut span is clipped, never dropped, and shows its full placeholder.
    Blocklist redaction is decided once over the whole document, as ``apply``
    does, not per chunk: a range edge inside a token run never exposes a
    fragment of a run that would be redacted whole.

    Raises ``ValueError`` for an invalid range or span, or when
    ``resolved_spans`` do not match ``detection.spans`` as ``apply`` resolved
    them (fail closed: a shifted or missing span would leave a value raw), and
    ``TypeError`` for a non-integer bound, before producing any output.
    """
    text = detection.text
    ordered = _checked_against_detection(detection, resolved_spans)
    checked = _validated_ranges(text, ranges)
    edits = _edits(detection, ordered, anon_map)
    ends = [edit.end for edit in edits]

    chunks: List[ProjectedChunk] = []
    for chunk_start, chunk_end in checked:
        parts: List[str] = []
        projected: List[ProjectedSpan] = []
        cursor = chunk_start
        for edit in edits[bisect_right(ends, chunk_start) :]:
            if edit.start >= chunk_end:
                break
            parts.append(text[cursor : max(edit.start, chunk_start)])
            parts.append(edit.text)
            if edit.span is not None:
                projected.append(_projected(edit.span, text, chunk_start, chunk_end))
            cursor = min(edit.end, chunk_end)
        parts.append(text[cursor:chunk_end])
        chunks.append(ProjectedChunk(chunk_start, chunk_end, "".join(parts), tuple(projected)))
    return chunks
