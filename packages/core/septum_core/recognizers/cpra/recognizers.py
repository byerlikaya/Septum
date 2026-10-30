from __future__ import annotations

"""Recognizer pack for the CPRA regulation.

CPRA extends CCPA; most identifiers (SSN, credit card, phone, email,
address) are handled by the regulation-agnostic baseline. Entity-type
coverage and legal basis are documented in
``packages/core/docs/REGULATION_ENTITY_SOURCES.md``.
"""

from typing import List

from presidio_analyzer import EntityRecognizer

from ...text_boundaries import token_bounded
from ..base_recognizer import (
    RegexPatternConfig,
    ValidatedPatternRecognizer,
)


def _drivers_license_recognizer() -> EntityRecognizer:
    """Format-only driver's licence: one letter followed by seven digits."""
    return ValidatedPatternRecognizer(
        entity_type="DRIVERS_LICENSE",
        config=RegexPatternConfig(
            name="cpra_drivers_license",
            pattern=token_bounded(r"[A-Z]\d{7}"),
            score=0.55,
        ),
    )


def get_recognizers() -> List[EntityRecognizer]:
    """Return all CPRA-specific recognizers."""
    recognizers: List[EntityRecognizer] = []
    recognizers.append(_drivers_license_recognizer())
    return recognizers
