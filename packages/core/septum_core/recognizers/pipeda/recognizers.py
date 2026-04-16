from __future__ import annotations

"""Recognizer pack for the PIPEDA regulation.

Adds a format-only Social Insurance Number (SIN) recognizer plus an
email detector on top of the regulation-agnostic baseline. Entity-
type coverage and legal basis are documented in
``packages/core/docs/REGULATION_ENTITY_SOURCES.md``.
"""

from typing import List

from presidio_analyzer import EntityRecognizer

from ..base_recognizer import RegexPatternConfig, ValidatedPatternRecognizer


def _social_insurance_number_recognizer() -> EntityRecognizer:
    """Format-only SIN: nine digits, optionally grouped as XXX-XXX-XXX."""
    return ValidatedPatternRecognizer(
        entity_type="NATIONAL_ID",
        config=RegexPatternConfig(
            name="pipeda_sin",
            pattern=r"\b\d{3}[-\s]?\d{3}[-\s]?\d{3}\b",
            score=0.55,
        ),
    )


def _email_recognizer() -> EntityRecognizer:
    return ValidatedPatternRecognizer(
        entity_type="EMAIL_ADDRESS",
        config=RegexPatternConfig(
            name="pipeda_email",
            pattern=r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[A-Za-z]{2,}\b",
            score=0.8,
        ),
    )


def get_recognizers() -> List[EntityRecognizer]:
    """Return all PIPEDA-specific recognizers."""
    recognizers: List[EntityRecognizer] = []
    recognizers.append(_social_insurance_number_recognizer())
    recognizers.append(_email_recognizer())
    return recognizers
