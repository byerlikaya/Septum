from __future__ import annotations

"""Recognizer pack for the CCPA regulation.

Most CCPA-relevant identifiers (SSN, credit card, phone, email,
address) are handled by the regulation-agnostic baseline. This pack
adds state-issued driver's license format detection on top. Entity-
type coverage and legal basis are documented in
``packages/core/docs/REGULATION_ENTITY_SOURCES.md``.
"""

from typing import List

from presidio_analyzer import EntityRecognizer

from ..base_recognizer import (
    RegexPatternConfig,
    ValidatedPatternRecognizer,
)


def _drivers_license_recognizer() -> EntityRecognizer:
    """Format-only driver's licence: one letter followed by seven digits."""
    return ValidatedPatternRecognizer(
        entity_type="DRIVERS_LICENSE",
        config=RegexPatternConfig(
            name="ccpa_drivers_license",
            pattern=r"\b[A-Z]\d{7}\b",
            score=0.55,
        ),
    )


def get_recognizers() -> List[EntityRecognizer]:
    """Return all CCPA-specific recognizers."""
    recognizers: List[EntityRecognizer] = []
    recognizers.append(_drivers_license_recognizer())
    return recognizers
