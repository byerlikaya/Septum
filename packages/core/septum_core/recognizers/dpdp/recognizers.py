from __future__ import annotations

"""Recognizer pack for the DPDP regulation.

Entity-type coverage and legal basis are documented in
``backend/docs/REGULATION_ENTITY_SOURCES.md``.
"""

from typing import List

from presidio_analyzer import EntityRecognizer

from ...national_ids.aadhaar import AadhaarValidator
from ..base_recognizer import RegexPatternConfig, ValidatedPatternRecognizer

_AADHAAR_VALIDATOR = AadhaarValidator()


def _aadhaar_recognizer() -> EntityRecognizer:
    """12-digit Aadhaar recognizer with Verhoeff checksum validation.

    A checksum-valid Aadhaar is emitted at the Presidio-promoted
    ``1.0``; a 12-digit sequence in the correct format that fails
    Verhoeff is still surfaced at ``0.55`` so synthetic or typo'd
    Aadhaar numbers are masked rather than silently leaked.
    """
    return ValidatedPatternRecognizer(
        entity_type="NATIONAL_ID",
        config=RegexPatternConfig(
            name="dpdp_aadhaar_12digit",
            pattern=r"\b[2-9]\d{3}\s?\d{4}\s?\d{4}\b",
            score=0.75,
            fallback_score=0.55,
        ),
        algorithmic_validator=lambda raw: _AADHAAR_VALIDATOR.validate(
            raw.replace(" ", "")
        ),
    )


def _email_recognizer() -> EntityRecognizer:
    return ValidatedPatternRecognizer(
        entity_type="EMAIL_ADDRESS",
        config=RegexPatternConfig(
            name="dpdp_email",
            pattern=r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[A-Za-z]{2,}\b",
            score=0.8,
        ),
    )


def get_recognizers() -> List[EntityRecognizer]:
    """Return all DPDP-specific recognizers."""
    recognizers: List[EntityRecognizer] = []
    recognizers.append(_aadhaar_recognizer())
    recognizers.append(_email_recognizer())
    return recognizers
