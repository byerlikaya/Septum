from __future__ import annotations

"""HIPAA-specific Presidio recognizers."""

from typing import List

from presidio_analyzer import EntityRecognizer

from ..base_recognizer import RegexPatternConfig, ValidatedPatternRecognizer


def _medical_record_number_recognizer() -> EntityRecognizer:
    # Highly simplified medical record number pattern; in real deployments this
    # should be specialized per institution.
    return ValidatedPatternRecognizer(
        entity_type="MEDICAL_RECORD_NUMBER",
        config=RegexPatternConfig(
            name="hipaa_mrn",
            pattern=r"\\bMRN[- ]?[0-9]{6,10}\\b",
            score=0.8,
        ),
    )


def _health_insurance_id_recognizer() -> EntityRecognizer:
    return ValidatedPatternRecognizer(
        entity_type="HEALTH_INSURANCE_ID",
        config=RegexPatternConfig(
            name="hipaa_insurance_id",
            pattern=r"\\b[A-Z]{2}[0-9]{6,10}\\b",
            score=0.75,
        ),
    )


def _email_recognizer() -> EntityRecognizer:
    return ValidatedPatternRecognizer(
        entity_type="EMAIL_ADDRESS",
        config=RegexPatternConfig(
            name="hipaa_email",
            pattern=r"\\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[A-Za-z]{2,}\\b",
            score=0.8,
        ),
    )


def get_recognizers() -> List[EntityRecognizer]:
    """Return all HIPAA-specific recognizers."""
    recognizers: List[EntityRecognizer] = []
    recognizers.append(_medical_record_number_recognizer())
    recognizers.append(_health_insurance_id_recognizer())
    recognizers.append(_email_recognizer())
    return recognizers

