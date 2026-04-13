from __future__ import annotations

"""Recognizer pack for the LGPD regulation.

CPF detection lives in the regulation-agnostic baseline (algorithmic
checksum), so this pack only contributes format-driven extras such as
email and CNPJ detection. Entity-type coverage and legal basis are
documented in ``backend/docs/REGULATION_ENTITY_SOURCES.md``.
"""

from typing import List

from presidio_analyzer import EntityRecognizer

from ..base_recognizer import RegexPatternConfig, ValidatedPatternRecognizer


def _cnpj_recognizer() -> EntityRecognizer:
    """Format-only CNPJ (corporate tax ID) recognizer.

    Matches the structural XX.XXX.XXX/XXXX-XX layout; algorithmic
    checksum validation is intentionally left out until a CNPJ
    validator is added under ``services/national_ids/``.
    """
    return ValidatedPatternRecognizer(
        entity_type="TAX_ID",
        config=RegexPatternConfig(
            name="lgpd_cnpj",
            pattern=r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b",
            score=0.6,
        ),
    )


def _email_recognizer() -> EntityRecognizer:
    return ValidatedPatternRecognizer(
        entity_type="EMAIL_ADDRESS",
        config=RegexPatternConfig(
            name="lgpd_email",
            pattern=r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[A-Za-z]{2,}\b",
            score=0.8,
        ),
    )


def get_recognizers() -> List[EntityRecognizer]:
    """Return all LGPD-specific recognizers."""
    recognizers: List[EntityRecognizer] = []
    recognizers.append(_cnpj_recognizer())
    recognizers.append(_email_recognizer())
    return recognizers
