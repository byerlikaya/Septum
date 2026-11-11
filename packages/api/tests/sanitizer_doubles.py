"""Sanitizer doubles shared by the PII gate tests."""

import re
from typing import Tuple

from septum_api.services.anonymization_map import AnonymizationMap
from septum_core.detector import Detector
from septum_core.spans import DetectedSpan, DetectionResult, ResolvedSpan, SanitizeResult

Rule = Tuple[re.Pattern[str], str]


class PatternSanitizer:
    """Reports every match of each rule as an entity of the rule's type.

    A rule's span is its first capture group when the pattern has one, otherwise
    the whole match.
    """

    def __init__(self, *rules: Rule) -> None:
        self._rules = rules

    def sanitize(self, text: str, language: str, anon_map: AnonymizationMap) -> SanitizeResult:
        spans = []
        for pattern, entity_type in self._rules:
            for match in pattern.finditer(text):
                start, end = match.span(1) if pattern.groups else match.span()
                spans.append(ResolvedSpan(start, end, entity_type, "[X_1]", 1.0))
        return SanitizeResult(sanitized_text=text, entity_count=len(spans), detected_spans=spans)


def literal(value: str, entity_type: str) -> Rule:
    """Return a rule that detects every occurrence of ``value``."""
    return re.compile(re.escape(value)), entity_type


class PatternDetector:
    """Detector double with the ``detect`` / ``apply`` split of the core detector.

    ``detect`` reports every match of each rule (first capture group when the
    pattern has one) as a span of the rule's type; ``apply`` assigns
    placeholders through the core replacement step, then the blocklist, as
    the real detector does. ``calls`` records ``(method, text length)``.
    """

    def __init__(self, *rules: Rule) -> None:
        self._rules = rules
        self.calls: list[tuple[str, int]] = []

    def detect(self, text: str, language: str, *, boundaries=()) -> DetectionResult:
        self.calls.append(("detect", len(text)))
        spans = []
        for pattern, entity_type in self._rules:
            for match in pattern.finditer(text):
                start, end = match.span(1) if pattern.groups else match.span()
                spans.append(DetectedSpan(start, end, entity_type, 0.9))
        return DetectionResult(
            text=text, language=language, spans=tuple(spans), semantic_enabled=False
        )

    def apply(self, detection: DetectionResult, anon_map: AnonymizationMap) -> SanitizeResult:
        self.calls.append(("apply", len(detection.text)))
        masked, count, resolved = Detector._apply_replacements(
            detection.text, list(detection.spans), anon_map, detection.language
        )
        counts: dict[str, int] = {}
        for span in detection.spans:
            counts[span.entity_type] = counts.get(span.entity_type, 0) + 1
        return SanitizeResult(
            sanitized_text=anon_map.apply_blocklist(masked, detection.language),
            entity_count=count,
            entity_type_counts=counts,
            detected_spans=resolved,
        )

