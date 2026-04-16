from __future__ import annotations

"""Recognizer pack for the POPIA regulation.

Adds a format-only 13-digit South African national ID recognizer on
top of the regulation-agnostic baseline. Entity-type coverage and
legal basis are documented in
``packages/api/docs/REGULATION_ENTITY_SOURCES.md``.
"""

from typing import List

from presidio_analyzer import EntityRecognizer

from ..base_recognizer import RegexPatternConfig, ValidatedPatternRecognizer


def _national_id_recognizer() -> EntityRecognizer:
    """Format-only 13-digit national ID.

    Matches both the contiguous form ``8803175432087`` and the common
    printed / statement form ``880317 5432 087`` / ``880317-5432-087``
    (six digit YYMMDD + four digit sequence + three-digit checksum
    group, optionally separated by a single space or dash).
    """
    return ValidatedPatternRecognizer(
        entity_type="NATIONAL_ID",
        config=RegexPatternConfig(
            name="popia_national_id",
            pattern=r"\b\d{6}[\s\-]?\d{4}[\s\-]?\d{3}\b",
            score=0.6,
        ),
    )


def _email_recognizer() -> EntityRecognizer:
    return ValidatedPatternRecognizer(
        entity_type="EMAIL_ADDRESS",
        config=RegexPatternConfig(
            name="popia_email",
            pattern=r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[A-Za-z]{2,}\b",
            score=0.8,
        ),
    )


def get_recognizers() -> List[EntityRecognizer]:
    """Return all POPIA-specific recognizers."""
    recognizers: List[EntityRecognizer] = []
    recognizers.append(_national_id_recognizer())
    recognizers.append(_email_recognizer())
    return recognizers
