from __future__ import annotations

"""Recognizer pack for the APPI regulation.

Adds a format-only 12-digit My Number recognizer on top of the
regulation-agnostic baseline. Entity-type coverage and legal basis
are documented in ``packages/core/docs/REGULATION_ENTITY_SOURCES.md``.
"""

from typing import List

from presidio_analyzer import EntityRecognizer

from ..base_recognizer import RegexPatternConfig, ValidatedPatternRecognizer


def _my_number_recognizer() -> EntityRecognizer:
    """Format-only 12-digit national ID, optionally grouped as 4-4-4."""
    return ValidatedPatternRecognizer(
        entity_type="NATIONAL_ID",
        config=RegexPatternConfig(
            name="appi_my_number",
            pattern=r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}\b",
            score=0.6,
        ),
    )


def _email_recognizer() -> EntityRecognizer:
    return ValidatedPatternRecognizer(
        entity_type="EMAIL_ADDRESS",
        config=RegexPatternConfig(
            name="appi_email",
            pattern=r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[A-Za-z]{2,}\b",
            score=0.8,
        ),
    )


def get_recognizers() -> List[EntityRecognizer]:
    """Return all APPI-specific recognizers."""
    recognizers: List[EntityRecognizer] = []
    recognizers.append(_my_number_recognizer())
    recognizers.append(_email_recognizer())
    return recognizers
