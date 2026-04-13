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
    """11-digit national ID recognizer with checksum validation."""
    return ValidatedPatternRecognizer(
        entity_type="NATIONAL_ID",
        config=RegexPatternConfig(
            name="kvkk_national_id_11digit",
            pattern=r"\b[1-9][0-9]{10}\b",
            score=0.7,
        ),
        algorithmic_validator=_NATIONAL_ID_VALIDATOR.validate,
    )


def _contextual_national_id_recognizer() -> EntityRecognizer:
    """Context-assisted NATIONAL_ID recognizer.

    Looks for 8-12 digit sequences preceded by a national-ID context
    keyword (see ``_NATIONAL_ID_CONTEXT_KEYWORDS``). The capture group
    narrows the reported entity span to the digits alone, and the
    configured checksum validator keeps algorithmically invalid numbers
    out of the result.
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


def _phone_recognizer() -> EntityRecognizer:
    """Phone number recognizer for KVKK compliance.

    Detects phone numbers with optional country code prefix.
    Pattern: optional +XX prefix followed by 10 digits.
    """
    return ValidatedPatternRecognizer(
        entity_type="PHONE_NUMBER",
        config=RegexPatternConfig(
            name="kvkk_phone",
            pattern=r"\b\+?[0-9]{2}[0-9]{10}\b",
            score=0.75,
        ),
    )


def get_recognizers() -> List[EntityRecognizer]:
    """Return all KVKK-specific recognizers."""
    recognizers: List[EntityRecognizer] = []
    recognizers.append(_validated_national_id_recognizer())
    recognizers.append(_email_recognizer())
    recognizers.append(_phone_recognizer())
    recognizers.append(_contextual_national_id_recognizer())
    return recognizers

