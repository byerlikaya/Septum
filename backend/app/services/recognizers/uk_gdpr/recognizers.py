from __future__ import annotations

"""Recognizer pack for the uk_gdpr regulation.

Adds a structural National Insurance Number (NINO) recognizer on top
of the regulation-agnostic baseline. Entity-type coverage and legal
basis are documented in ``backend/docs/REGULATION_ENTITY_SOURCES.md``.
"""

from typing import List

from presidio_analyzer import EntityRecognizer

from ..base_recognizer import RegexPatternConfig, ValidatedPatternRecognizer


def _national_insurance_number_recognizer() -> EntityRecognizer:
    """Format-only NINO: two letters, six digits, final letter A-D."""
    return ValidatedPatternRecognizer(
        entity_type="NATIONAL_ID",
        config=RegexPatternConfig(
            name="uk_gdpr_nino",
            pattern=r"\b[A-CEGHJ-PR-TW-Z][A-CEGHJ-NPR-TW-Z]\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b",
            score=0.75,
        ),
    )


def _email_recognizer() -> EntityRecognizer:
    return ValidatedPatternRecognizer(
        entity_type="EMAIL_ADDRESS",
        config=RegexPatternConfig(
            name="uk_gdpr_email",
            pattern=r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[A-Za-z]{2,}\b",
            score=0.8,
        ),
    )


def get_recognizers() -> List[EntityRecognizer]:
    """Return all uk_gdpr-specific recognizers."""
    recognizers: List[EntityRecognizer] = []
    recognizers.append(_national_insurance_number_recognizer())
    recognizers.append(_email_recognizer())
    return recognizers
