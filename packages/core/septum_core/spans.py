from __future__ import annotations

"""Span data types shared across septum-core detection layers."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class DetectedSpan:
    """Represents a PII span detected by any layer of the sanitizer.

    ``core`` is the sub-range of ``[start, end)`` that names the entity. A
    partial overlap can widen a span over a token tail ("’nın" of "Kaya’nın");
    the whole range is masked, but only ``core`` is registered in the
    anonymization map. ``None`` means the whole range is the entity.
    """

    start: int
    end: int
    entity_type: str
    score: float
    core: Optional[Tuple[int, int]] = field(default=None, compare=False)


@dataclass
class ResolvedSpan:
    """A finalized PII span with its assigned placeholder after deduplication."""

    start: int
    end: int
    entity_type: str
    placeholder: str
    score: float
    core: Optional[Tuple[int, int]] = field(default=None, compare=False)


@dataclass
class SanitizeResult:
    """Result of applying the PII sanitizer to an input string."""

    sanitized_text: str
    entity_count: int
    entity_type_counts: Dict[str, int] = field(default_factory=dict)
    detected_spans: List[ResolvedSpan] = field(default_factory=list)


def clip_core(
    start: int, end: int, core: Optional[Tuple[int, int]]
) -> Tuple[int, int]:
    """``core`` clipped to ``[start, end)``; the whole range when it is None."""
    if core is None:
        return start, end
    return max(core[0], start), min(core[1], end)


def entity_range(
    start: int, end: int, core: Optional[Tuple[int, int]], text: str
) -> Tuple[int, int]:
    """Text range that names the entity over ``[start, end)`` in ``text``.

    The clipped ``core``, or the whole range when the clipped core is empty or
    whitespace-only.
    """
    core_start, core_end = clip_core(start, end, core)
    if core_start >= core_end or not text[core_start:core_end].strip():
        return start, end
    return core_start, core_end
