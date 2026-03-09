from __future__ import annotations

"""
Session-scoped anonymization map with language-aware coreference resolution.

This module keeps an in-memory mapping between original PII values and
placeholder tokens, plus a token-to-placeholder map so residual token
mentions are redacted with the correct [ENTITY_TYPE_N] placeholder.

The map is intentionally kept local to the current process and is never
persisted to disk in order to avoid leaking raw PII.
"""

from dataclasses import dataclass, field
from typing import Dict, Optional, Set
import re

from ..utils.text_utils import normalize_for_comparison

# Short common English words that must never be masked (no [BLOCKED], no entity).
SANITIZER_STOPWORDS: frozenset[str] = frozenset({"the", "a", "an", "of", "in", "at"})


@dataclass
class AnonymizationMap:
    """In-memory anonymization map with language-aware coreference support.

    The map tracks original entity strings and the placeholder assigned to
    them, plus a token-to-placeholder mapping used when applying the blocklist:
    residual token mentions are replaced with the correct [ENTITY_TYPE_N]
    placeholder, never with a generic [BLOCKED]. Tokens in SANITIZER_STOPWORDS
    are never added to the blocklist and are never redacted.
    """

    document_id: int
    language: str = "en"
    entity_map: Dict[str, str] = field(default_factory=dict)
    blocklist: Set[str] = field(default_factory=set)
    token_to_placeholder: Dict[str, str] = field(default_factory=dict)
    token_counter: Dict[str, int] = field(default_factory=dict)

    def add_entity(self, original: str, entity_type: str) -> str:
        """Register an entity span and return the placeholder for it.

        Coreference resolution is applied so that if ``original`` can be
        matched to a previously seen value (for example, "John" after
        "John Smith"), the existing placeholder is reused.
        """
        if not original:
            raise ValueError("original must be a non-empty string.")
        if not entity_type:
            raise ValueError("entity_type must be a non-empty string.")

        existing = self._find_existing(original)
        if existing is not None:
            self.entity_map.setdefault(original, existing)
            self._update_blocklist(original, existing)
            return existing

        placeholder = self._next_placeholder(entity_type)
        self.entity_map[original] = placeholder
        self._update_blocklist(original, placeholder)
        return placeholder

    def apply_blocklist(self, text: str, language: Optional[str] = None) -> str:
        """Apply token-level redaction using token_to_placeholder.

        Tokens that match a known entity token are replaced with the
        corresponding [ENTITY_TYPE_N] placeholder. Placeholder format only.
        SANITIZER_STOPWORDS are never redacted.
        """
        if not text or not self.token_to_placeholder:
            return text

        lang = language or self.language
        pattern = re.compile(r"\[[^\]]+\]|\w+", re.UNICODE)

        parts: list[str] = []
        last_end = 0

        for match in pattern.finditer(text):
            start, end = match.span()
            if start > last_end:
                parts.append(text[last_end:start])

            token = match.group(0)
            if token.startswith("[") and token.endswith("]"):
                parts.append(token)
            else:
                normalized = normalize_for_comparison(token, lang)
                if normalized in SANITIZER_STOPWORDS:
                    parts.append(token)
                elif normalized in self.token_to_placeholder:
                    parts.append(self.token_to_placeholder[normalized])
                else:
                    parts.append(token)

            last_end = end

        if last_end < len(text):
            parts.append(text[last_end:])

        return "".join(parts)

    def _next_placeholder(self, entity_type: str) -> str:
        """Return the next placeholder token for the given entity type."""
        count = self.token_counter.get(entity_type, 0) + 1
        self.token_counter[entity_type] = count
        return f"[{entity_type}_{count}]"

    def _update_blocklist(self, original: str, placeholder: str) -> None:
        """Update blocklist and token_to_placeholder from ``original``."""
        normalized = normalize_for_comparison(original, self.language)
        for token in normalized.split():
            if len(token) <= 2:
                continue
            if token in SANITIZER_STOPWORDS:
                continue
            self.blocklist.add(token)
            self.token_to_placeholder[token] = placeholder

    def _find_existing(self, original: str) -> Optional[str]:
        """Try to find an existing placeholder for ``original``.

        This uses normalized token overlap as a simple coreference heuristic.
        If the tokens of ``original`` are a subset of those of an existing
        entity (or vice versa), the same placeholder is reused.
        """
        if not self.entity_map:
            return None

        normalized = normalize_for_comparison(original, self.language)
        orig_tokens = {t for t in normalized.split() if len(t) > 2}

        for existing, placeholder in self.entity_map.items():
            existing_normalized = normalize_for_comparison(existing, self.language)
            if normalized == existing_normalized:
                return placeholder

            existing_tokens = {t for t in existing_normalized.split() if len(t) > 2}
            if not orig_tokens or not existing_tokens:
                continue

            if orig_tokens.issubset(existing_tokens) or existing_tokens.issubset(orig_tokens):
                return placeholder

        return None

