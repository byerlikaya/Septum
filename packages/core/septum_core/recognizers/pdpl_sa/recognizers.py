from __future__ import annotations

"""Recognizer pack for the pdpl_sa regulation.

Adds a format-only 10-digit national ID recognizer (resident prefix
``1``, non-resident prefix ``2``) on top of the regulation-agnostic
baseline. Entity-type coverage and legal basis are documented in
``packages/core/docs/REGULATION_ENTITY_SOURCES.md``.
"""

from typing import List

from presidio_analyzer import EntityRecognizer

from ..base_recognizer import (
    RegexPatternConfig,
    ValidatedPatternRecognizer,
)


def _national_id_recognizer() -> EntityRecognizer:
    """Format-only 10-digit national ID."""
    return ValidatedPatternRecognizer(
        entity_type="NATIONAL_ID",
        config=RegexPatternConfig(
            name="pdpl_sa_national_id",
            pattern=r"\b[12]\d{9}\b",
            score=0.55,
        ),
    )


def get_recognizers() -> List[EntityRecognizer]:
    """Return all pdpl_sa-specific recognizers."""
    recognizers: List[EntityRecognizer] = []
    recognizers.append(_national_id_recognizer())
    return recognizers
