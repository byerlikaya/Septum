"""Text normalization helpers for Septum.

These utilities provide Unicode normalization and simple locale-aware
lowercasing suitable for case-insensitive comparisons in a multilingual
environment.

Locale-specific casing rules handle edge cases like:
- Dotted/dotless I (İ→i, I→ı in certain locales)
- German ß handling
- Other script-specific case folding

All transformations use ISO 639-1 language codes as keys.
"""

from __future__ import annotations

import unicodedata
from typing import Dict

# Locale-specific character transformations for case folding.
# Uses ISO 639-1 codes. Add new locales as needed without modifying function logic.
_LOCALE_CASING_RULES: Dict[str, Dict[int, int]] = {
    "tr": str.maketrans("İI", "ii"),  # Dotted I → lowercase i, Dotless I → lowercase ı
    "az": str.maketrans("İI", "ii"),  # Azerbaijani uses same dotted-I rule
}


def normalize_unicode(text: str) -> str:
    """Normalize text to Unicode NFC form."""
    return unicodedata.normalize("NFC", text)


def strip_control_characters(text: str) -> str:
    """Remove non-printable control characters while preserving whitespace.

    The function keeps standard whitespace characters (space, tab, newlines)
    and strips characters in the Unicode "Other" categories, which include
    non-printable control codes that can corrupt extracted text.
    """

    def _is_allowed(ch: str) -> bool:
        if ch in {" ", "\t", "\n", "\r"}:
            return True
        category = unicodedata.category(ch)
        return not category.startswith("C")

    return "".join(ch for ch in text if _is_allowed(ch))


def locale_lower(text: str, language: str = "en") -> str:
    """Lowercase ``text`` using locale-aware casing rules.

    Args:
        text: Input text to lowercase
        language: ISO 639-1 language code (e.g., "en", "tr", "de")

    Returns:
        Lowercased text with locale-specific transformations applied.

    The function is intentionally conservative and only applies
    transformations for languages where ``str.lower()`` is insufficient.
    Falls back to standard lowercasing for unlisted languages.
    """
    transform = _LOCALE_CASING_RULES.get(language)
    if transform is not None:
        return text.translate(transform).lower()
    return text.lower()


def normalize_for_comparison(text: str, language: str = "en") -> str:
    """Normalize and lowercase ``text`` for case-insensitive comparison."""
    return locale_lower(normalize_unicode(text), language)

