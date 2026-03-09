"""Text normalization helpers for Septum.

These utilities provide Unicode normalization and simple locale-aware
lowercasing suitable for case-insensitive comparisons in a multilingual
environment.
"""

from __future__ import annotations

import unicodedata
from typing import Dict

# Locale-specific transforms used before lowercasing. The keys are ISO 639-1
# language codes. The values are translation maps for str.translate.
_LOCALE_TRANSFORMS: Dict[str, Dict[int, int]] = {
    # Turkish has special casing rules for dotted and dotless I.
    # This transform maps the problematic uppercase characters before .lower().
    "tr": str.maketrans("İI", "ii"),
}


def normalize_unicode(text: str) -> str:
    """Normalize text to Unicode NFC form."""
    return unicodedata.normalize("NFC", text)


def locale_lower(text: str, language: str = "en") -> str:
    """Lowercase ``text`` using simple locale-aware rules.

    The function is intentionally conservative and only special-cases
    languages where the default ``str.lower`` behavior is not sufficient.
    """
    transform = _LOCALE_TRANSFORMS.get(language)
    if transform is not None:
        return text.translate(transform).lower()
    return text.lower()


def normalize_for_comparison(text: str, language: str = "en") -> str:
    """Normalize and lowercase ``text`` for case-insensitive comparison."""
    return locale_lower(normalize_unicode(text), language)

