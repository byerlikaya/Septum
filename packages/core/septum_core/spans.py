from __future__ import annotations

"""Span data types shared across septum-core detection layers."""

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class DetectedSpan:
    """Represents a PII span detected by any layer of the sanitizer."""

    start: int
    end: int
    entity_type: str
    score: float


@dataclass
class ResolvedSpan:
    """A finalized PII span with its assigned placeholder after deduplication."""

    start: int
    end: int
    entity_type: str
    placeholder: str
    score: float


@dataclass
class SanitizeResult:
    """Result of applying the PII sanitizer to an input string."""

    sanitized_text: str
    entity_count: int
    entity_type_counts: Dict[str, int] = field(default_factory=dict)
    detected_spans: List[ResolvedSpan] = field(default_factory=list)
