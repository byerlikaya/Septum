from __future__ import annotations

"""Non-PII filter layer for sanitizer spans.

This module provides a data-driven filter that removes spans which should be
treated as non-PII according to configured rules. Rules are language- and
entity-aware but never hardcode any particular language or regulation in
code; all behavior is driven by caller-supplied configuration.
"""

import re
from dataclasses import dataclass
from typing import Iterable, List, Sequence

from .anonymization_map import SANITIZER_STOPWORDS
from .regulations.models import NonPiiRuleLike
from .text_utils import normalize_for_comparison


@dataclass
class SpanView:
    """Lightweight view of a detected span used by the NonPiiFilter."""

    start: int
    end: int
    entity_type: str
    score: float


@dataclass
class _CompiledRule:
    pattern_type: str  # "token" | "regex"
    pattern: str
    languages: Sequence[str]
    entity_types: Sequence[str]
    min_score: float | None


class NonPiiFilter:
    """Filter out spans which should be treated as non-PII.

    The filter uses a set of compiled rules derived from :class:`NonPiiRuleLike`
    records. Rules can match on normalized token values or regular expressions.
    Only very obvious non-PII spans (for example greetings or boilerplate)
    should be configured here; ambiguous cases should remain masked.
    """

    def __init__(self, rules: Iterable[NonPiiRuleLike]) -> None:
        self._rules: List[_CompiledRule] = []
        for rule in rules:
            if not rule.is_active:
                continue
            pattern_type = (rule.pattern_type or "").strip().lower()
            if pattern_type not in {"token", "regex"}:
                continue
            if not rule.pattern:
                continue
            self._rules.append(
                _CompiledRule(
                    pattern_type=pattern_type,
                    pattern=rule.pattern,
                    languages=list(rule.languages or []),
                    entity_types=list(rule.entity_types or []),
                    min_score=rule.min_score,
                )
            )

    @classmethod
    def from_rules(cls, rules: Iterable[NonPiiRuleLike]) -> "NonPiiFilter | None":
        """Build a filter from rules, returning None if there are no active ones."""
        instance = cls(rules)
        return instance if instance._rules else None

    def filter_spans(
        self,
        text: str,
        language: str,
        spans: list[SpanView],
    ) -> list[SpanView]:
        """Return spans after removing those classified as non-PII by rules.

        This method is conservative by design: if no rule applies, the span is
        preserved. Only spans that clearly match an explicit non-PII rule are
        removed.
        """
        if not spans:
            return spans

        lang_norm = (language or "").strip().lower()
        keep: list[SpanView] = []

        for span in spans:
            span_text = text[span.start : span.end]
            norm = normalize_for_comparison(span_text.strip(), lang_norm)
            if not norm:
                keep.append(span)
                continue

            if norm in SANITIZER_STOPWORDS:
                continue

            if self._rules and self._matches_any_rule(norm, lang_norm, span):
                continue

            keep.append(span)

        return keep

    def _matches_any_rule(
        self,
        normalized_text: str,
        language: str,
        span: SpanView,
    ) -> bool:
        for rule in self._rules:
            if rule.languages and language not in {l.lower() for l in rule.languages}:
                continue
            if rule.entity_types and span.entity_type not in rule.entity_types:
                continue
            if rule.min_score is not None and span.score < rule.min_score:
                continue

            if rule.pattern_type == "token":
                if normalized_text == normalize_for_comparison(rule.pattern, language):
                    return True
            elif rule.pattern_type == "regex":
                try:
                    if re.search(rule.pattern, normalized_text):
                        return True
                except re.error:
                    continue

        return False
