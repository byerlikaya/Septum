from __future__ import annotations

"""Recognizer pack for the PIPEDA regulation.

Adds a format-only Social Insurance Number (SIN) recognizer on top of
the regulation-agnostic baseline, which also detects e-mail. Entity-
type coverage and legal basis are documented in
``packages/core/docs/REGULATION_ENTITY_SOURCES.md``.
"""

from typing import List

from presidio_analyzer import EntityRecognizer

from ...text_boundaries import token_bounded
from ..base_recognizer import (
    RegexPatternConfig,
    ValidatedPatternRecognizer,
)


def _social_insurance_number_recognizer() -> EntityRecognizer:
    """Format-only SIN: nine digits, optionally grouped as XXX-XXX-XXX."""
    return ValidatedPatternRecognizer(
        entity_type="NATIONAL_ID",
        config=RegexPatternConfig(
            name="pipeda_sin",
            pattern=token_bounded(r"\d{3}[-\s]?\d{3}[-\s]?\d{3}"),
            score=0.55,
        ),
    )


def get_recognizers() -> List[EntityRecognizer]:
    """Return all PIPEDA-specific recognizers."""
    recognizers: List[EntityRecognizer] = []
    recognizers.append(_social_insurance_number_recognizer())
    return recognizers
