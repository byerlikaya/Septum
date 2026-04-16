from __future__ import annotations

"""Recognizer pack for the PIPL regulation.

Adds a format-only 18-character Resident Identity Card recognizer on
top of the regulation-agnostic baseline (17 digits plus a check
character that may be ``X``). Entity-type coverage and legal basis
are documented in ``packages/core/docs/REGULATION_ENTITY_SOURCES.md``.
"""

from typing import List

from presidio_analyzer import EntityRecognizer

from ..base_recognizer import RegexPatternConfig, ValidatedPatternRecognizer


def _resident_id_recognizer() -> EntityRecognizer:
    """Format-only 18-char Resident ID: 17 digits plus digit-or-X."""
    return ValidatedPatternRecognizer(
        entity_type="NATIONAL_ID",
        config=RegexPatternConfig(
            name="pipl_resident_id",
            pattern=r"\b\d{17}[\dXx]\b",
            score=0.7,
        ),
    )


def _email_recognizer() -> EntityRecognizer:
    return ValidatedPatternRecognizer(
        entity_type="EMAIL_ADDRESS",
        config=RegexPatternConfig(
            name="pipl_email",
            pattern=r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[A-Za-z]{2,}\b",
            score=0.8,
        ),
    )


def get_recognizers() -> List[EntityRecognizer]:
    """Return all PIPL-specific recognizers."""
    recognizers: List[EntityRecognizer] = []
    recognizers.append(_resident_id_recognizer())
    recognizers.append(_email_recognizer())
    return recognizers
