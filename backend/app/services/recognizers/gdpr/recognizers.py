from __future__ import annotations

"""GDPR-specific Presidio recognizers."""

from typing import Iterable, List

from presidio_analyzer import EntityRecognizer

from ..base_recognizer import RegexPatternConfig, ValidatedPatternRecognizer


def _email_recognizer() -> EntityRecognizer:
    return ValidatedPatternRecognizer(
        entity_type="EMAIL_ADDRESS",
        config=RegexPatternConfig(
            name="gdpr_email",
            pattern=r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[A-Za-z]{2,}\b",
            score=0.8,
        ),
    )


def _ip_address_recognizer() -> EntityRecognizer:
    return ValidatedPatternRecognizer(
        entity_type="IP_ADDRESS",
        config=RegexPatternConfig(
            name="gdpr_ipv4",
            pattern=(
                r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}"
                r"(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\b"
            ),
            score=0.8,
        ),
    )


def _phone_recognizer() -> EntityRecognizer:
    return ValidatedPatternRecognizer(
        entity_type="PHONE_NUMBER",
        config=RegexPatternConfig(
            name="gdpr_phone",
            pattern=r"\b\+?[0-9]{7,15}\b",
            score=0.7,
        ),
    )


def get_recognizers() -> List[EntityRecognizer]:
    """Return all GDPR-specific recognizers."""
    recognizers: List[EntityRecognizer] = []
    recognizers.append(_email_recognizer())
    recognizers.append(_ip_address_recognizer())
    recognizers.append(_phone_recognizer())
    return recognizers

