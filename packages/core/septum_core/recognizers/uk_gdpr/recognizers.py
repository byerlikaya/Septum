from __future__ import annotations

"""Recognizer pack for the uk_gdpr regulation.

Adds a structural National Insurance Number (NINO) recognizer on top
of the regulation-agnostic baseline. Entity-type coverage and legal
basis are documented in ``packages/core/docs/REGULATION_ENTITY_SOURCES.md``.
"""

from typing import List

from presidio_analyzer import EntityRecognizer

from ...text_boundaries import token_bounded
from ..base_recognizer import (
    RegexPatternConfig,
    ValidatedPatternRecognizer,
)


def _national_insurance_number_recognizer() -> EntityRecognizer:
    """Format-only NINO: two letters, six digits, final letter A-D."""
    return ValidatedPatternRecognizer(
        entity_type="NATIONAL_ID",
        config=RegexPatternConfig(
            name="uk_gdpr_nino",
            pattern=token_bounded(r"[A-CEGHJ-PR-TW-Z][A-CEGHJ-NPR-TW-Z]\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]"),
            score=0.75,
        ),
    )


def get_recognizers() -> List[EntityRecognizer]:
    """Return all uk_gdpr-specific recognizers."""
    recognizers: List[EntityRecognizer] = []
    recognizers.append(_national_insurance_number_recognizer())
    return recognizers
