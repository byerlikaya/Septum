from __future__ import annotations

"""Recognizer pack for the CPRA regulation.

CPRA extends CCPA; most identifiers (SSN, credit card, phone, email,
address) are handled by the regulation-agnostic baseline. Entity-type
coverage and legal basis are documented in
``backend/docs/REGULATION_ENTITY_SOURCES.md``.
"""

from typing import List

from presidio_analyzer import EntityRecognizer

from ..base_recognizer import RegexPatternConfig, ValidatedPatternRecognizer


def _drivers_license_recognizer() -> EntityRecognizer:
    """Format-only driver's licence: one letter followed by seven digits."""
    return ValidatedPatternRecognizer(
        entity_type="DRIVERS_LICENSE",
        config=RegexPatternConfig(
            name="cpra_drivers_license",
            pattern=r"\b[A-Z]\d{7}\b",
            score=0.55,
        ),
    )


def _email_recognizer() -> EntityRecognizer:
    return ValidatedPatternRecognizer(
        entity_type="EMAIL_ADDRESS",
        config=RegexPatternConfig(
            name="cpra_email",
            pattern=r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[A-Za-z]{2,}\b",
            score=0.8,
        ),
    )


def get_recognizers() -> List[EntityRecognizer]:
    """Return all CPRA-specific recognizers."""
    recognizers: List[EntityRecognizer] = []
    recognizers.append(_drivers_license_recognizer())
    recognizers.append(_email_recognizer())
    return recognizers
