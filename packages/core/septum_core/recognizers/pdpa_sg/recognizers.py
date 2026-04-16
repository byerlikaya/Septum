from __future__ import annotations

"""Recognizer pack for the pdpa_sg regulation.

Adds a format-only NRIC/FIN recognizer on top of the regulation-
agnostic baseline. Entity-type coverage and legal basis are
documented in ``packages/api/docs/REGULATION_ENTITY_SOURCES.md``.
"""

from typing import List

from presidio_analyzer import EntityRecognizer

from ..base_recognizer import RegexPatternConfig, ValidatedPatternRecognizer


def _nric_fin_recognizer() -> EntityRecognizer:
    """Format-only NRIC/FIN: letter prefix, 7 digits, final letter."""
    return ValidatedPatternRecognizer(
        entity_type="NATIONAL_ID",
        config=RegexPatternConfig(
            name="pdpa_sg_nric_fin",
            pattern=r"\b[STFGM]\d{7}[A-Z]\b",
            score=0.7,
        ),
    )


def _email_recognizer() -> EntityRecognizer:
    return ValidatedPatternRecognizer(
        entity_type="EMAIL_ADDRESS",
        config=RegexPatternConfig(
            name="pdpa_sg_email",
            pattern=r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[A-Za-z]{2,}\b",
            score=0.8,
        ),
    )


def get_recognizers() -> List[EntityRecognizer]:
    """Return all pdpa_sg-specific recognizers."""
    recognizers: List[EntityRecognizer] = []
    recognizers.append(_nric_fin_recognizer())
    recognizers.append(_email_recognizer())
    return recognizers
