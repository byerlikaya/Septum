from __future__ import annotations

"""Recognizer pack for the KVKK regulation.

Entity-type coverage and legal basis are documented in
``backend/docs/REGULATION_ENTITY_SOURCES.md``.
"""

from typing import List

from presidio_analyzer import EntityRecognizer

from ...national_ids.tckn import TCKNValidator
from ..base_recognizer import RegexPatternConfig, ValidatedPatternRecognizer

_NATIONAL_ID_VALIDATOR = TCKNValidator()

# FUTURE: move to DB as a per-regulation context-keyword list so each
# regulation pack can declare its own preamble vocabulary without editing
# code. Kept minimal and ASCII-only until then.
_NATIONAL_ID_CONTEXT_KEYWORDS: tuple[str, ...] = (
    "id",
    "national",
    "citizen",
    "passport",
    "identity",
    "identification",
    "no",
)


def _validated_national_id_recognizer() -> EntityRecognizer:
    """11-digit national ID recognizer with checksum validation.

    A passing TCKN is emitted at the full Presidio-promoted score of
    ``1.0``; a syntactically valid 11-digit sequence that fails the
    checksum is still surfaced at a reduced ``0.55`` so
    synthetic / typo'd / test data is masked rather than silently
    leaked. Privacy-first: over-detection is cheaper than a PII leak.
    """
    return ValidatedPatternRecognizer(
        entity_type="NATIONAL_ID",
        config=RegexPatternConfig(
            name="kvkk_national_id_11digit",
            pattern=r"\b[1-9][0-9]{10}\b",
            score=0.7,
            fallback_score=0.55,
        ),
        algorithmic_validator=_NATIONAL_ID_VALIDATOR.validate,
    )


def _contextual_national_id_recognizer() -> EntityRecognizer:
    """Context-assisted NATIONAL_ID recognizer.

    Looks for 8-12 digit sequences preceded by a national-ID context
    keyword (see ``_NATIONAL_ID_CONTEXT_KEYWORDS``). The capture group
    narrows the reported entity span to the digits alone. The TCKN
    checksum is still run, but when it fails the match is kept at a
    reduced ``0.5`` fallback score — the context keyword is a strong
    signal that the digits are an identifier, so over-detection is
    preferred over silently dropping synthetic data.
    """
    keyword_alternation = "|".join(_NATIONAL_ID_CONTEXT_KEYWORDS)
    return ValidatedPatternRecognizer(
        entity_type="NATIONAL_ID",
        config=RegexPatternConfig(
            name="kvkk_national_id_context",
            pattern=(
                rf"(?i)\b(?:{keyword_alternation})\b"
                r"[^\d]{0,16}(\d{8,12})"
            ),
            score=0.6,
            narrow_to_group=1,
            fallback_score=0.5,
        ),
        algorithmic_validator=_NATIONAL_ID_VALIDATOR.validate,
    )


def _email_recognizer() -> EntityRecognizer:
    return ValidatedPatternRecognizer(
        entity_type="EMAIL_ADDRESS",
        config=RegexPatternConfig(
            name="kvkk_email",
            pattern=r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[A-Za-z]{2,}\b",
            score=0.8,
        ),
    )


def get_recognizers() -> List[EntityRecognizer]:
    """Return all kvkk-specific recognizers.

    Phone detection is intentionally delegated to the baseline
    ``ExtendedPhoneRecognizer`` in ``services/sanitizer.py`` — a
    pack-level ``\\b\\+?[0-9]{12}\\b`` pattern silently gobbled
    11-12 digit national-ID numbers (TCKN, Iqama, Japan My Number
    prefixes) whenever the `+` was optional, and there is no
    regulation-specific phone format that the baseline misses.
    """
    recognizers: List[EntityRecognizer] = []
    recognizers.append(_validated_national_id_recognizer())
    recognizers.append(_email_recognizer())
    recognizers.append(_contextual_national_id_recognizer())
    return recognizers

