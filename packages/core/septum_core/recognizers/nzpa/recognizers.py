from __future__ import annotations

"""Recognizer pack for the NZPA regulation.

Adds a format-only 8-9 digit IRD number recognizer on top of the
regulation-agnostic baseline. Entity-type coverage and legal basis
are documented in ``packages/api/docs/REGULATION_ENTITY_SOURCES.md``.
"""

from typing import List

from presidio_analyzer import EntityRecognizer

from ..base_recognizer import RegexPatternConfig, ValidatedPatternRecognizer


def _ird_number_recognizer() -> EntityRecognizer:
    """Format-only IRD number: 8 or 9 digits, optionally dashed."""
    return ValidatedPatternRecognizer(
        entity_type="TAX_ID",
        config=RegexPatternConfig(
            name="nzpa_ird",
            pattern=r"\b\d{2,3}[-\s]?\d{3}[-\s]?\d{3}\b",
            score=0.55,
        ),
    )


def _email_recognizer() -> EntityRecognizer:
    return ValidatedPatternRecognizer(
        entity_type="EMAIL_ADDRESS",
        config=RegexPatternConfig(
            name="nzpa_email",
            pattern=r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[A-Za-z]{2,}\b",
            score=0.8,
        ),
    )


def get_recognizers() -> List[EntityRecognizer]:
    """Return all NZPA-specific recognizers."""
    recognizers: List[EntityRecognizer] = []
    recognizers.append(_ird_number_recognizer())
    recognizers.append(_email_recognizer())
    return recognizers
