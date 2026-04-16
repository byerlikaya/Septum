from __future__ import annotations

"""Recognizer pack for the australia_pa regulation.

Adds format-only Tax File Number and Medicare card recognizers on top
of the regulation-agnostic baseline. Entity-type coverage and legal
basis are documented in ``packages/api/docs/REGULATION_ENTITY_SOURCES.md``.
"""

from typing import List

from presidio_analyzer import EntityRecognizer

from ..base_recognizer import RegexPatternConfig, ValidatedPatternRecognizer


def _tax_file_number_recognizer() -> EntityRecognizer:
    """Format-only TFN: 8 or 9 digits in 3-3-3 or 3-3-2 grouping."""
    return ValidatedPatternRecognizer(
        entity_type="TAX_ID",
        config=RegexPatternConfig(
            name="australia_pa_tfn",
            pattern=r"\b\d{3}[-\s]?\d{3}[-\s]?\d{2,3}\b",
            score=0.55,
        ),
    )


def _medicare_card_recognizer() -> EntityRecognizer:
    """Format-only Medicare card: 10 digits in 4-5-1 grouping."""
    return ValidatedPatternRecognizer(
        entity_type="HEALTH_INSURANCE_ID",
        config=RegexPatternConfig(
            name="australia_pa_medicare",
            pattern=r"\b\d{4}[-\s]?\d{5}[-\s]?\d\b",
            score=0.6,
        ),
    )


def _email_recognizer() -> EntityRecognizer:
    return ValidatedPatternRecognizer(
        entity_type="EMAIL_ADDRESS",
        config=RegexPatternConfig(
            name="australia_pa_email",
            pattern=r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[A-Za-z]{2,}\b",
            score=0.8,
        ),
    )


def get_recognizers() -> List[EntityRecognizer]:
    """Return all australia_pa-specific recognizers."""
    recognizers: List[EntityRecognizer] = []
    recognizers.append(_tax_file_number_recognizer())
    recognizers.append(_medicare_card_recognizer())
    recognizers.append(_email_recognizer())
    return recognizers
