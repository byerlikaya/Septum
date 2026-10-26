"""Sanitizer doubles shared by the PII gate tests."""

import re
from typing import Tuple

from septum_api.services.anonymization_map import AnonymizationMap
from septum_core.spans import ResolvedSpan, SanitizeResult

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
