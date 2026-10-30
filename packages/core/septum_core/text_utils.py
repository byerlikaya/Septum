"""Text normalization helpers for Septum.

These utilities provide Unicode normalization and simple locale-aware
lowercasing suitable for case-insensitive comparisons in a multilingual
environment.

Locale-specific casing rules handle edge cases such as the dotted /
dotless I (``İ→i``, ``I→ı``) in certain locales, sharp-s / eszett
folding, and other script-specific case-fold irregularities that the
default ``str.lower`` does not cover. All transformations use
ISO 639-1 language codes as keys.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Dict, Sequence

from .language_resolution import canonical_language_code

_WHITESPACE_RUN = re.compile(r"\s+")

# Locale-specific character transformations for case folding.
# Uses ISO 639-1 codes. Add new locales as needed without modifying function logic.
_LOCALE_CASING_RULES: Dict[str, Dict[int, int]] = {
    "tr": str.maketrans("İI", "ii"),
    "az": str.maketrans("İI", "ii"),
}


def normalize_unicode(text: str) -> str:
    """Normalize text to Unicode NFC form."""
    return unicodedata.normalize("NFC", text)


# Code points Unicode marks Default_Ignorable_Code_Point that are not category
# Cf (DerivedCoreProperties.txt): Hangul fillers, the combining grapheme joiner,
# Khmer inherent vowels, Mongolian and other variation selectors, and reserved
# ranges. The braille pattern blank renders as empty space too. All of them
# can sit inside a token and leave no visible trace.
_INVISIBLE_RANGES: tuple[tuple[int, int], ...] = (
    (0x034F, 0x034F), (0x115F, 0x1160), (0x17B4, 0x17B5), (0x180B, 0x180F),
    (0x2065, 0x2065), (0x2800, 0x2800), (0x3164, 0x3164), (0xFE00, 0xFE0F), (0xFFA0, 0xFFA0),
    (0xFFF0, 0xFFF8), (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF),
)

# Full stops that NFKC leaves as they are but that read as "." between labels.
_EXTRA_FOLDS: dict[str, str] = {"。": ".", "｡": "."}


def _is_invisible(ch: str) -> bool:
    """Whether ``ch`` renders as nothing: a format character or a default-ignorable one."""
    if unicodedata.category(ch) == "Cf":
        return True
    code = ord(ch)
    return any(lo <= code <= hi for lo, hi in _INVISIBLE_RANGES)


def compatibility_fold(text: str) -> tuple[str, Sequence[int]]:
    """Fold ``text`` for pattern matching and map every folded character back.

    Each character is folded to its NFKC form on its own, so fullwidth and
    other presentation forms (``＠``, ``ｊｏｈｎ``, ``．``) read as their plain
    characters, and the ideographic full stops ``。`` and ``｡`` read as ``.``.
    Invisible characters are dropped: format characters (category ``Cf``:
    zero-width space and joiners, soft hyphen, direction marks) and the other
    default-ignorable ones (Hangul fillers, variation selectors, the braille
    blank). The second value gives, for every folded character, the index of
    the character in ``text`` it came from, so a match on the folded text maps
    back to the original offsets.
    """
    if text.isascii():
        return text, range(len(text))
    parts: list[str] = []
    origin: list[int] = []
    for index, ch in enumerate(text):
        if ch.isascii():
            parts.append(ch)
            origin.append(index)
            continue
        if _is_invisible(ch):
            continue
        folded = _EXTRA_FOLDS.get(ch) or unicodedata.normalize("NFKC", ch)
        parts.append(folded)
        origin.extend([index] * len(folded))
    return "".join(parts), origin


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
        language: ISO 639-1 language code (e.g., "en", "tr", "de"); region
            tags such as ``tr-TR`` resolve to their base code

    Returns:
        Lowercased text with locale-specific transformations applied.

    The function is intentionally conservative and only applies
    transformations for languages where ``str.lower()`` is insufficient.
    Falls back to standard lowercasing for unlisted languages.
    """
    transform = _LOCALE_CASING_RULES.get(canonical_language_code(language))
    if transform is not None:
        return text.translate(transform).lower()
    return text.lower()


def normalize_for_comparison(text: str, language: str = "en") -> str:
    """Normalize and lowercase ``text`` for case-insensitive comparison.

    Beyond NFC + locale-aware case folding, this also collapses any run
    of whitespace (including tabs, newlines and non-breaking U+00A0) to
    a single ASCII space and trims edges, so that ``"Ahmet  Çelik"``,
    ``"ahmet çelik"`` and ``" Ahmet Çelik "`` all compare equal.
    Without this, the anonymization map would mint distinct
    placeholders for the same person across PDF/OCR-extracted spans.
    """
    cased = locale_lower(normalize_unicode(text), language)
    return _WHITESPACE_RUN.sub(" ", cased).strip()


# Possessive suffixes to strip, keyed by ISO 639-1 language code.
# Entries are checked longest-first to avoid partial matches.
_POSSESSIVE_SUFFIXES: Dict[str, list[str]] = {
    "tr": ["'ın", "'in", "'un", "'ün", "'nın", "'nin", "'nun", "'nün"],
    "az": ["'ın", "'in", "'un", "'ün"],
}

# Apostrophe characters that word processors, keyboards and OCR produce for
# the same mark: ASCII, U+2018 and U+2019 (single quotation marks), U+02BC
# (modifier letter apostrophe), U+00B4 (acute accent), U+2032 (prime) and
# U+FF07 (fullwidth apostrophe). Backtick is left out: it is not used as an
# apostrophe. Shared by the suffix matching here and the token folding in
# ``span_processing``.
APOSTROPHES: frozenset[str] = frozenset({"'", "‘", "’", "ʼ", "´", "′", "＇"})

# Maps every apostrophe variant to ASCII. The mapping is one character to one
# character, so offsets in the folded text match the original.
_APOSTROPHE_FOLD = str.maketrans(dict.fromkeys(APOSTROPHES, "'"))

# Suffixes shared across most Latin-script languages.
_COMMON_POSSESSIVE_SUFFIXES = ["'s"]


def _ends_with_folded(text: str, suffix: str) -> bool:
    """Whether ``text`` ends with ``suffix`` after folding apostrophe variants."""
    return text[-len(suffix) :].translate(_APOSTROPHE_FOLD) == suffix


def strip_possessive_suffix(text: str, language: str = "en") -> str:
    """Remove possessive/genitive suffixes for coreference resolution.

    Looks up language-specific suffixes via ``_POSSESSIVE_SUFFIXES``
    keyed by ISO 639-1 code, then falls back to the shared suffixes in
    ``_COMMON_POSSESSIVE_SUFFIXES``. Apostrophe variants (see ``APOSTROPHES``)
    match the same suffixes as ASCII. The returned text keeps the original
    characters. Idempotent and pure.
    """
    if not text or len(text) < 3:
        return text

    for suffix in _POSSESSIVE_SUFFIXES.get(canonical_language_code(language), []):
        if _ends_with_folded(text, suffix):
            return text[: -len(suffix)]

    for suffix in _COMMON_POSSESSIVE_SUFFIXES:
        if _ends_with_folded(text, suffix):
            return text[: -len(suffix)]

    # Plural possessive ("James'"). Only the apostrophe goes: the stem keeps its "s".
    if _ends_with_folded(text, "s'"):
        return text[:-1]

    return text


def starts_with_uppercase(text: str) -> bool:
    """Return True if text starts with an uppercase letter.

    Script-agnostic: returns True for characters without case (CJK, Arabic,
    etc.) so that non-cased scripts are never rejected by callers that use
    this as a proper-noun heuristic.
    """
    if not text:
        return False
    first_char = text[0]
    has_case = first_char.upper() != first_char.lower()
    return not has_case or first_char.isupper()
