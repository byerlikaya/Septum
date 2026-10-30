from __future__ import annotations

"""HIPAA-specific Presidio recognizers."""

from typing import List

from presidio_analyzer import EntityRecognizer

from ...text_boundaries import token_bounded
from ..base_recognizer import (
    RegexPatternConfig,
    ValidatedPatternRecognizer,
)


def _medical_record_number_recognizer() -> EntityRecognizer:
    return ValidatedPatternRecognizer(
        entity_type="MEDICAL_RECORD_NUMBER",
        config=RegexPatternConfig(
            name="hipaa_mrn",
            pattern=token_bounded(r"MRN[- ]?[0-9]{6,10}"),
            score=0.8,
        ),
    )


def _health_insurance_id_recognizer() -> EntityRecognizer:
    return ValidatedPatternRecognizer(
        entity_type="HEALTH_INSURANCE_ID",
        config=RegexPatternConfig(
            name="hipaa_insurance_id",
            pattern=token_bounded(r"[A-Z]{2}[0-9]{6,10}"),
            score=0.75,
        ),
    )


def get_recognizers() -> List[EntityRecognizer]:
    """Return all HIPAA-specific recognizers."""
    recognizers: List[EntityRecognizer] = []
    recognizers.append(_medical_record_number_recognizer())
    recognizers.append(_health_insurance_id_recognizer())
    return recognizers

