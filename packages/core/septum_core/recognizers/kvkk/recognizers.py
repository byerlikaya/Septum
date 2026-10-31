from __future__ import annotations

"""Recognizer pack for the KVKK regulation.

Entity-type coverage and legal basis are documented in
``packages/core/docs/REGULATION_ENTITY_SOURCES.md``.
"""

from typing import List, Tuple

from presidio_analyzer import EntityRecognizer

from ...national_ids.tckn import TCKNValidator
from ...text_boundaries import (
    CueTable,
    cue_alternation,
    literal_cue,
    token_bounded,
    whole_cue,
)
from ..base_recognizer import (
    RegexPatternConfig,
    ValidatedPatternRecognizer,
)

_NATIONAL_ID_VALIDATOR = TCKNValidator()

# FUTURE: move to DB as a per-regulation context-keyword list so each
# regulation pack can declare its own preamble vocabulary without editing
# code. Keyed by ISO 639-1 code; every entry is a plain word, matched as a
# whole word when it has two letters or fewer ("id", "no") and between cue
# edges otherwise (see ``literal_cue``).
_NATIONAL_ID_CONTEXT_KEYWORDS: CueTable = {
    "en": ("id", "national", "citizen", "passport", "identity", "identification", "no"),
}

# Glued or abbreviated ID labels ("kimlikNo", "TCKimlikNo", "IDNo"): a cue of
# two letters or fewer never counts at a case step, so the whole label is the cue.
_NATIONAL_ID_LABELS: CueTable = {
    "tr": (r"(?:T\.?\s*C\.?\s*)?Kimlik\s*(?:No|Numaras[ıi])",),
    "en": (r"ID\s*(?:No|Number)",),
}

# Labels in front of a legal entity's tax number (Vergi Kimlik Numarası).
_TAX_ID_LABELS: CueTable = {
    "tr": (r"Vergi\s*(?:Kimlik\s*)?No", "VKN"),
}


# Labels in front of a customer / membership reference code.
_CUSTOMER_REFERENCE_LABELS: CueTable = {
    "tr": (
        r"M[üu]şteri(?:\s+(?:No|Numaras[ıi]))?",
        r"[ÜU]ye(?:\s+(?:No|Numaras[ıi]))?",
        r"CRM(?:\s+No)?",
        r"Hesap\s+No",
    ),
}


def _token_alternation(table: CueTable, endings: Tuple[str, ...] = ("s",)) -> str:
    """Every regex entry of ``table`` as one alternation that matches between cue edges only."""
    return whole_cue(cue_alternation(table), endings)


def _keyword_alternation(table: CueTable) -> str:
    """Every plain-word entry of ``table`` as one alternation of :func:`literal_cue` cues."""
    words = (word for entries in table.values() for word in entries)
    return "(?:" + "|".join(literal_cue(word) for word in words) + ")"


def _validated_national_id_recognizer() -> EntityRecognizer:
    """11-digit national ID recognizer with checksum validation.

    A passing TCKN is emitted at the full Presidio-promoted score of
    ``1.0``; a syntactically valid 11-digit sequence that fails the
    checksum is still surfaced at a reduced ``0.55`` so
    synthetic / typo'd / test data is masked rather than silently
    leaked. Privacy-first: over-detection is cheaper than a PII leak.
    """
    return ValidatedPatternRecognizer(
        entity_type="NATIONAL_ID",
        config=RegexPatternConfig(
            name="kvkk_national_id_11digit",
            pattern=token_bounded(r"[1-9][0-9]{10}"),
            score=0.7,
            fallback_score=0.55,
        ),
        algorithmic_validator=_NATIONAL_ID_VALIDATOR.validate,
    )


def _contextual_national_id_recognizer() -> EntityRecognizer:
    """Context-assisted NATIONAL_ID recognizer.

    Looks for 8-12 digit sequences preceded by a national-ID context
    keyword or label (see ``_NATIONAL_ID_CONTEXT_KEYWORDS`` and
    ``_NATIONAL_ID_LABELS``). The capture group
    narrows the reported entity span to the digits alone. The TCKN
    checksum is still run, but when it fails the match is kept at a
    reduced ``0.5`` fallback score — the context keyword is a strong
    signal that the digits are an identifier, so over-detection is
    preferred over silently dropping synthetic data.
    """
    return ValidatedPatternRecognizer(
        entity_type="NATIONAL_ID",
        config=RegexPatternConfig(
            name="kvkk_national_id_context",
            pattern=(
                rf"(?i)(?:{_token_alternation(_NATIONAL_ID_LABELS, endings=())}"
                rf"|{_keyword_alternation(_NATIONAL_ID_CONTEXT_KEYWORDS)})"
                r"[^\d]{0,16}(\d{8,12})"
            ),
            score=0.6,
            narrow_to_group=1,
            fallback_score=0.5,
        ),
        algorithmic_validator=_NATIONAL_ID_VALIDATOR.validate,
    )


def _label_prefixed_national_id_recognizer() -> EntityRecognizer:
    """Turkish-label NATIONAL_ID recognizer for TCKN / Kimlik forms.

    Matches sequences like ``T.C. 29374810562``, ``TC 29374810562``,
    ``TCKN: 29374810562``, ``T.C. Kimlik No: 29374810562`` and the
    plain ``Kimlik No 29374810562`` pattern. The dotted abbreviation
    ``T.C.`` is not reachable through the keyword-based contextual
    recognizer because its tokens contain punctuation, so it needs
    its own regex. Optional whitespace inside the label tolerates
    PDF extraction quirks. Checksum validation is still applied;
    failures stay visible at a reduced ``0.5`` fallback score since
    the explicit label is itself a strong identifier signal.
    """
    pattern = (
        r"(?i)(?:T\s*\.?\s*C\s*\.?"
        r"(?:\s+Kimlik(?:\s+No)?)?"
        r"|TCKN"
        r"|Kimlik\s+No)"
        r"[^\d]{0,16}(\d{10,12})"
    )
    return ValidatedPatternRecognizer(
        entity_type="NATIONAL_ID",
        config=RegexPatternConfig(
            name="kvkk_national_id_tr_label",
            pattern=pattern,
            score=0.6,
            narrow_to_group=1,
            fallback_score=0.5,
        ),
        algorithmic_validator=_NATIONAL_ID_VALIDATOR.validate,
    )


def _label_prefixed_tax_id_recognizer() -> EntityRecognizer:
    """Turkish-label TAX_ID recognizer for ``Vergi No`` / ``VKN``.

    Turkish VKN (Vergi Kimlik Numarası) is a 10-digit tax identifier
    for legal entities. A dedicated recognizer keeps the placeholder
    semantically correct (``[TAX_ID_N]`` rather than ``[NATIONAL_ID_N]``
    that the generic ``no`` context keyword would otherwise produce).
    No checksum — algorithmic VKN validation is not yet implemented
    and privacy-first over-detection is preferable to a silent miss.
    """
    return ValidatedPatternRecognizer(
        entity_type="TAX_ID",
        config=RegexPatternConfig(
            name="kvkk_tax_id_tr_label",
            pattern=(
                rf"(?i){_token_alternation(_TAX_ID_LABELS)}"
                r"[^\d]{0,16}(\d{10})"
            ),
            score=0.6,
            narrow_to_group=1,
        ),
    )


def _customer_reference_recognizer() -> EntityRecognizer:
    """Customer / membership reference IDs after Turkish-label cues.

    KVKK Art. 3 defines kişisel veri broadly as any data linked to an
    identifiable person, which covers customer / membership numbers
    that pin a record to one individual (loyalty IDs, CRM accounts,
    portal user codes). Forms in the wild use prefixed alphanumeric
    codes like ``ETP-2021-00489`` or ``CRM-12345``; the contextual
    cue is one of ``Müşteri No``, ``Üye No``, ``Hesap No`` (when
    not already covered by BANK_ACCOUNT_NUMBER), ``CRM No``, ``Üye
    Numarası``, ``Müşteri Numarası``, with optional whitespace
    around the colon and an optional ``(varsa)`` qualifier as seen
    on real KVKK consent forms.
    """
    pattern = (
        rf"(?i){_token_alternation(_CUSTOMER_REFERENCE_LABELS, endings=())}"
        r"(?:\s*\([^)]+\))?"   # optional "(varsa)" / "(opsiyonel)" qualifier
        r"\s*:?\s*:?\s*"
        r"([A-Z]{2,8}[-/]\d{2,4}[-/]\d{3,8})"
    )
    return ValidatedPatternRecognizer(
        entity_type="CUSTOMER_REFERENCE_ID",
        config=RegexPatternConfig(
            name="kvkk_customer_reference_tr_label",
            pattern=pattern,
            score=0.75,
            narrow_to_group=1,
        ),
    )


def get_recognizers() -> List[EntityRecognizer]:
    """Return all kvkk-specific recognizers.

    Phone detection is intentionally delegated to the baseline
    ``ExtendedPhoneRecognizer`` in ``services/sanitizer.py`` — a
    pack-level ``\\b\\+?[0-9]{12}\\b`` pattern silently gobbled
    11-12 digit national-ID numbers (TCKN, Iqama, Japan My Number
    prefixes) whenever the `+` was optional, and there is no
    regulation-specific phone format that the baseline misses.
    """
    recognizers: List[EntityRecognizer] = []
    recognizers.append(_validated_national_id_recognizer())
    recognizers.append(_contextual_national_id_recognizer())
    recognizers.append(_label_prefixed_national_id_recognizer())
    recognizers.append(_label_prefixed_tax_id_recognizer())
    recognizers.append(_customer_reference_recognizer())
    return recognizers

