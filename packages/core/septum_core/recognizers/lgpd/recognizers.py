from __future__ import annotations

"""Recognizer pack for the lgpd regulation.

CPF detection lives in the regulation-agnostic baseline (algorithmic
checksum), so this pack contributes format-driven extras: CNPJ and
civil-identity document numbers.
Entity-type coverage and legal basis are documented in
``packages/core/docs/REGULATION_ENTITY_SOURCES.md``.
"""

from typing import List

from presidio_analyzer import EntityRecognizer

from ...text_boundaries import token_bounded, whole_cue, word_cue
from ..base_recognizer import (
    RegexPatternConfig,
    ValidatedPatternRecognizer,
)

# FUTURE: move to DB. Context preamble for the Brazilian civil-identity
# document (Registro Geral); the abbreviations match only between cue edges.
# "rg" is too short to count at a case step, so the glued field name
# ("rgNumero", "rg_number", "rgNo") is a cue of its own.
_CIVIL_IDENTITY_CONTEXT = (
    word_cue("rg"),
    word_cue(r"r\.\s*g\.", endings=()),
    r"registro\s+geral",
    r"identidade",
    whole_cue("ssp|cpf/rg"),
    whole_cue(r"rg_?(?:n[uú]mero|number|no)", endings=()),
)

# A number sign that may sit between the cue and the value
# ("RG nº", "RG n.º", "RG nr", "RG número").
_NUMBER_SIGN = r"n\.?\s*[º°o]\.?|n\.|nro?\.?|n[uú]m\.|n[uú]mero|number"


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
            pattern=token_bounded(r"\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}"),
            score=0.6,
        ),
    )


def _civil_identity_document_recognizer() -> EntityRecognizer:
    """Civil-identity document number (Brazilian Registro Geral).

    Matches both the dotted ``NN.NNN.NNN-X`` and compact
    ``NNNNNNNNX`` variants (the check character may be left out),
    with an optional issuing-authority suffix (``SSP/SP``) that is
    intentionally not captured in the narrowed span. Requires an
    RG-related context preamble, optionally
    followed by a number sign (``nº``, ``n.º``, ``nr``, ``número``).
    """
    ctx = "|".join(_CIVIL_IDENTITY_CONTEXT)
    return ValidatedPatternRecognizer(
        entity_type="NATIONAL_ID",
        config=RegexPatternConfig(
            name="lgpd_civil_identity_document",
            pattern=(
                rf"(?i)(?:{ctx})(?:[^\w]{{0,4}}(?:{_NUMBER_SIGN}))?[^\w]{{0,16}}"
                r"(\d{1,3}(?:\.\d{3}){2}(?:[\-\s]?[A-Z\d])?|\d{7,9}(?:[\-\s]?[A-Z\d])?)"
            ),
            score=0.8,
            narrow_to_group=1,
        ),
    )


def get_recognizers() -> List[EntityRecognizer]:
    """Return all lgpd-specific recognizers."""
    recognizers: List[EntityRecognizer] = []
    recognizers.append(_cnpj_recognizer())
    recognizers.append(_civil_identity_document_recognizer())
    return recognizers
