"""Anonymization map: original PII values and their placeholders.

The map records which placeholder (``[ENTITY_TYPE_N]``) stands for each
original value, plus a token-to-placeholder table so residual mentions
of a person's name are redacted with the same placeholder. Placeholder
reuse (coreference) is limited to person-identifying types; every other
value only shares a placeholder with an identical value of the same type.

The map holds raw PII. This package never writes it anywhere: it lives
in memory, and a host that keeps it across requests is responsible for
storing it encrypted (the Septum API keeps per-document maps in memory
and writes them AES-256-GCM encrypted to Redis and to disk). It must
never be sent to the frontend, to a cloud LLM, or to logs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .text_utils import normalize_for_comparison, strip_possessive_suffix

# FUTURE: migrate to NonPiiRule table for full user-editability.
# Minimal stopwords kept here for technical filtering (ultra-common function words).
SANITIZER_STOPWORDS: frozenset[str] = frozenset({"the", "a", "an", "of", "in", "at"})

# Entity types whose tokens should be propagated through the blocklist for
# coreference resolution.  Only person-identifying types qualify; other
# categories (locations, identifiers, …) would cause excessive collateral
# replacements of common words.
_BLOCKLIST_ENTITY_TYPES: frozenset[str] = frozenset({
    "PERSON_NAME", "FIRST_NAME", "LAST_NAME", "ALIAS", "USERNAME",
})


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
    # Optional placeholder→original lookup populated only by the chat-time
    # multi-document unification path. ``entity_map`` is keyed by original
    # so a single original cannot carry two distinct placeholders even
    # though the unification can legitimately assign two of them when the
    # same name was detected as different entity types across documents
    # (e.g. ``Antalya`` as LOCATION in one doc and ORGANIZATION_NAME in
    # another). The reversed lookup keeps every (placeholder, original)
    # pair so the deanonymizer can still resolve every placeholder the
    # cloud LLM might echo. Per-document maps leave this field empty and
    # the deanonymizer derives the lookup from ``entity_map`` directly.
    placeholder_lookup: Dict[str, str] = field(default_factory=dict)
    # Lookup indexes derived from ``entity_map``; never persisted. They are
    # rebuilt whenever ``entity_map`` changed size behind ``add_entity``
    # (callers that fill the map directly, deserialized maps).
    _value_index: Dict[Tuple[str, str], str] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )
    _person_entries: List[Tuple[Set[str], str]] = field(
        default_factory=list, init=False, repr=False, compare=False
    )
    _indexed_size: int = field(default=-1, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Reconstruct blocklist and lookup indexes when deserialized."""
        if self.token_to_placeholder and not self.blocklist:
            self.blocklist = set(self.token_to_placeholder.keys())
        self._sync_index()

    def add_entity(self, original: str, entity_type: str) -> str:
        """Register an entity span and return the placeholder for it.

        Coreference resolution is applied so that if ``original`` can be
        matched to a previously seen value (for example, "John" after
        "John Smith"), the existing placeholder is reused; see
        ``_find_existing`` for when a match is allowed.

        Only person-identifying entity types (see ``_BLOCKLIST_ENTITY_TYPES``)
        have their tokens propagated to the blocklist. Other entity types are
        recorded in the entity map but do not trigger blocklist expansion,
        which avoids collateral replacement of common words (e.g. city names).
        """
        if not original:
            raise ValueError("original must be a non-empty string.")
        if not entity_type:
            raise ValueError("entity_type must be a non-empty string.")

        existing = self._find_existing(original, entity_type)
        if existing is not None:
            if original not in self.entity_map:
                self.entity_map[original] = existing
                self._index_entry(original, existing)
            if entity_type in _BLOCKLIST_ENTITY_TYPES:
                self._update_blocklist(original, existing)
            return existing

        placeholder = self._next_placeholder(entity_type)
        self.entity_map[original] = placeholder
        self._index_entry(original, placeholder)
        if entity_type in _BLOCKLIST_ENTITY_TYPES:
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
        """Update blocklist and token_to_placeholder from ``original``.

        All tokens from detected entities are added to blocklist regardless of
        casing, except very short tokens (<=2 chars) and stopwords. This ensures
        residual mentions of person names (e.g. lowercase "sarah", "david") are
        redacted by apply_blocklist even when NER models miss them.

        Possessive suffixes are stripped before tokenization so that
        ``"Ahmet's"`` and ``"Ahmet"`` produce the same blocklist tokens.
        """
        stripped = strip_possessive_suffix(original, self.language)
        normalized = normalize_for_comparison(stripped, self.language)
        orig_tokens = stripped.split()
        norm_tokens = normalized.split()

        for index, token in enumerate(norm_tokens):
            if len(token) <= 2:
                continue
            if token in SANITIZER_STOPWORDS:
                continue

            original_token = orig_tokens[index] if index < len(orig_tokens) else ""
            if not original_token:
                continue

            self.blocklist.add(token)
            self.token_to_placeholder[token] = placeholder

    def _normalized(self, value: str) -> str:
        """Return the comparison form of ``value`` (possessive stripped)."""
        stripped = strip_possessive_suffix(value, self.language)
        return normalize_for_comparison(stripped, self.language)

    @staticmethod
    def _coreference_tokens(normalized: str) -> Set[str]:
        """Return the tokens used for person-name subset matching."""
        return {t for t in normalized.split() if len(t) > 2}

    def _index_entry(self, original: str, placeholder: str) -> None:
        """Add one ``entity_map`` entry to the lookup indexes."""
        entity_type = _placeholder_entity_type(placeholder)
        normalized = self._normalized(original)
        self._value_index.setdefault((entity_type, normalized), placeholder)
        if entity_type in _BLOCKLIST_ENTITY_TYPES:
            tokens = self._coreference_tokens(normalized)
            if tokens:
                self._person_entries.append((tokens, placeholder))
        self._indexed_size = len(self.entity_map)

    def _sync_index(self) -> None:
        """Rebuild the lookup indexes when ``entity_map`` changed directly."""
        if self._indexed_size == len(self.entity_map):
            return
        self._value_index = {}
        self._person_entries = []
        for original, placeholder in self.entity_map.items():
            self._index_entry(original, placeholder)
        self._indexed_size = len(self.entity_map)

    def _find_existing(self, original: str, entity_type: str) -> Optional[str]:
        """Try to find an existing placeholder for ``original``.

        An existing placeholder is reused when:

        * the original string is already a key of the map (the map is
          keyed by original, so a second placeholder for the same string
          would leave the first one unrestorable);
        * the normalized value equals an existing one of the same entity
          type (constant-time index lookup);
        * both values are person-identifying (``_BLOCKLIST_ENTITY_TYPES``)
          and the tokens of one are a subset of the other's, so ``"John"``
          after ``"John Smith"`` keeps the same placeholder. Only this
          case scans, and only over person entries.

        Any other overlap (dates sharing a month and year, a location
        inside an address, …) gets its own placeholder, because reusing
        it would restore the wrong value on de-anonymization. The entity
        type of an existing entry is read from its placeholder, so maps
        persisted before this rule need no migration.

        Possessive suffixes are stripped before comparison so that
        ``"Ahmet's"`` matches a previously seen ``"Ahmet"``.
        """
        if not self.entity_map:
            return None
        existing_placeholder = self.entity_map.get(original)
        if existing_placeholder is not None:
            return existing_placeholder

        self._sync_index()
        normalized = self._normalized(original)
        exact = self._value_index.get((entity_type, normalized))
        if exact is not None:
            return exact
        if entity_type not in _BLOCKLIST_ENTITY_TYPES:
            return None

        orig_tokens = self._coreference_tokens(normalized)
        if not orig_tokens:
            return None
        for tokens, placeholder in self._person_entries:
            if orig_tokens.issubset(tokens) or tokens.issubset(orig_tokens):
                return placeholder
        return None


def _placeholder_entity_type(placeholder: str) -> str:
    """Return the entity type encoded in a ``[ENTITY_TYPE_N]`` placeholder."""
    return placeholder.strip("[]").rpartition("_")[0]
