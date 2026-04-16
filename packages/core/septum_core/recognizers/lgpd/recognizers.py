from __future__ import annotations

"""Recognizer pack for the lgpd regulation.

CPF detection lives in the regulation-agnostic baseline (algorithmic
checksum), so this pack contributes format-driven extras: CNPJ,
civil-identity document numbers, and the common email pattern.
Entity-type coverage and legal basis are documented in
``packages/api/docs/REGULATION_ENTITY_SOURCES.md``.
"""

from typing import List

from presidio_analyzer import EntityRecognizer

from ..base_recognizer import RegexPatternConfig, ValidatedPatternRecognizer

# FUTURE: move to DB. Single hard-coded context preamble for the
# Brazilian civil-identity document (Registro Geral).
_CIVIL_IDENTITY_CONTEXT = (
    r"\brg\b",
    r"registro\s+geral",
    r"\b(?:ssp|cpf/rg)\b",
)


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


def _civil_identity_document_recognizer() -> EntityRecognizer:
    """Civil-identity document number (Brazilian Registro Geral).

    Matches both the dotted ``NN.NNN.NNN-X`` and compact
    ``NNNNNNNNX`` variants, with an optional issuing-authority
    suffix (``SSP/SP``) that is intentionally not captured in the
    narrowed span. Requires an RG-related context preamble.
    """
    ctx = "|".join(_CIVIL_IDENTITY_CONTEXT)
    return ValidatedPatternRecognizer(
        entity_type="NATIONAL_ID",
        config=RegexPatternConfig(
            name="lgpd_civil_identity_document",
            pattern=(
                rf"(?i)(?:{ctx})[^\w]{{0,16}}"
                r"(\d{1,3}(?:\.\d{3}){2}[\-\s]?[A-Z0-9]|\d{7,9}[\-\s]?[A-Z0-9])"
            ),
            score=0.8,
            narrow_to_group=1,
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
    """Return all lgpd-specific recognizers."""
    recognizers: List[EntityRecognizer] = []
    recognizers.append(_cnpj_recognizer())
    recognizers.append(_civil_identity_document_recognizer())
    recognizers.append(_email_recognizer())
    return recognizers
