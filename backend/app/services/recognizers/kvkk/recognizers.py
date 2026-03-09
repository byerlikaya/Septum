from __future__ import annotations

"""KVKK-specific Presidio recognizers."""

from typing import List

from presidio_analyzer import EntityRecognizer

from ..base_recognizer import RegexPatternConfig, ValidatedPatternRecognizer


def _turkish_national_id_recognizer() -> EntityRecognizer:
    # Structural pattern only; algorithmic validation is handled by the
    # dedicated TCKN validator-based recognizer in the sanitizer layer.
    return ValidatedPatternRecognizer(
        entity_type="NATIONAL_ID",
        config=RegexPatternConfig(
            name="kvkk_tckn",
            pattern=r"\\b[1-9][0-9]{10}\\b",
            score=0.7,
        ),
    )


def _email_recognizer() -> EntityRecognizer:
    return ValidatedPatternRecognizer(
        entity_type="EMAIL_ADDRESS",
        config=RegexPatternConfig(
            name="kvkk_email",
            pattern=r"\\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[A-Za-z]{2,}\\b",
            score=0.8,
        ),
    )


def _phone_recognizer() -> EntityRecognizer:
    return ValidatedPatternRecognizer(
        entity_type="PHONE_NUMBER",
        config=RegexPatternConfig(
            name="kvkk_phone",
            pattern=r"\\b\\+?90[0-9]{10}\\b",
            score=0.75,
        ),
    )


def get_recognizers() -> List[EntityRecognizer]:
    """Return all KVKK-specific recognizers."""
    recognizers: List[EntityRecognizer] = []
    recognizers.append(_turkish_national_id_recognizer())
    recognizers.append(_email_recognizer())
    recognizers.append(_phone_recognizer())
    return recognizers

