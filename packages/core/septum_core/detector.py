from __future__ import annotations

"""
PII detection pipeline for septum-core.

This module implements the multi-layer PII detection stack:

1. Presidio AnalyzerEngine with custom recognizers (Layer 1).
2. HuggingFace NER models selected via :class:`NERModelRegistry` (Layer 2).
3. Optional semantic validation layer (Layer 3) - filters false positives
   using a pluggable :class:`SemanticDetectionPort`.
4. Optional alias/nickname layer (Layer 4) - detects indirect references
   through the same port.

The actual string replacements are performed via :class:`AnonymizationMap`,
which ensures that placeholders are stable and that a token-level
blocklist is applied as a final safety net.

All network / LLM concerns are delegated to :class:`SemanticDetectionPort`
implementations attached by the host: the core package itself never
imports any network client, so it can run inside an air-gapped zone.
"""

import logging
import re
import threading
from typing import Dict, List, Optional

from presidio_analyzer import AnalyzerEngine, EntityRecognizer, RecognizerResult

from .anonymization_map import SANITIZER_STOPWORDS, AnonymizationMap
from .config import SeptumCoreConfig
from .national_ids import IBANValidator
from .ner_model_registry import NERModelRegistry
from .non_pii_filter import NonPiiFilter, SpanView
from .ports import NullSemanticDetectionPort, SemanticDetectionPort
from .regulations.composer import ComposedPolicy
from .span_processing import (
    deduplicate_spans,
    expand_person_name_spans,
    merge_adjacent_person_name_spans,
)
from .spans import DetectedSpan, ResolvedSpan, SanitizeResult
from .text_utils import normalize_for_comparison, normalize_unicode

_cached_nlp_engine: object | None = None
_nlp_engine_lock = threading.Lock()


def _get_shared_nlp_engine() -> object:
    """Return a process-wide Presidio NLP engine singleton.

    Uses ``en_core_web_sm`` instead of the default ``en_core_web_lg`` to
    reduce memory by ~700 MB (no 300-dim word vectors). Presidio's NER
    acts as a supplementary first layer; the dedicated HuggingFace NER
    layer provides the primary person/location detection.
    """
    global _cached_nlp_engine
    if _cached_nlp_engine is not None:
        return _cached_nlp_engine
    with _nlp_engine_lock:
        if _cached_nlp_engine is not None:
            return _cached_nlp_engine
        from presidio_analyzer.nlp_engine import NlpEngineProvider

        conf = {
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
        }
        _cached_nlp_engine = NlpEngineProvider(nlp_configuration=conf).create_engine()
        _adjust_spacy_max_length(_cached_nlp_engine)
        return _cached_nlp_engine


def _adjust_spacy_max_length(nlp_engine: object) -> None:
    """Increase spaCy max_length on all models inside the NLP engine."""
    nlp_store = getattr(nlp_engine, "nlp", None)
    if isinstance(nlp_store, dict):
        for model in nlp_store.values():
            if hasattr(model, "max_length"):
                current_max = getattr(model, "max_length", 0)
                if current_max < 2_000_000:
                    model.max_length = 2_000_000


logger = logging.getLogger(__name__)

_HIGH_PRIORITY_ENTITY_TYPES: set[str] = {
    # Structural identifiers that fight over overlapping digit spans.
    # All of these must live in the same dedup pool so the
    # ``_ENTITY_TYPE_PRIORITY`` tiebreak in ``deduplicate_spans``
    # can actually apply — otherwise CPF/SSN/TAX_ID etc. land in the
    # low-priority bucket and lose automatically to a PHONE_NUMBER
    # span that overlaps them from the high-priority bucket.
    "PHONE_NUMBER",
    "NATIONAL_ID",
    "IBAN",
    "CREDIT_CARD_NUMBER",
    "CPF",
    "SOCIAL_SECURITY_NUMBER",
    "TAX_ID",
    "PASSPORT_NUMBER",
    "DRIVERS_LICENSE",
    "MEDICAL_RECORD_NUMBER",
    "HEALTH_INSURANCE_ID",
    "LICENSE_PLATE",
}

_SEMANTIC_VALIDATION_PASSTHROUGH_TYPES: frozenset[str] = frozenset(
    _HIGH_PRIORITY_ENTITY_TYPES
)

_MIN_TEXT_LENGTH_FOR_NER = 50
_MIN_TEXT_LENGTH_FOR_SEMANTIC_ALIAS = 80

_UPPER_WORD_RE = re.compile(r"\b\w{2,}\b")


def _titlecase_upper_segments(text: str) -> str:
    """Convert ALL CAPS words to title case for NER processing.

    Transformer NER models are trained on mixed-case text and perform
    poorly on ALL CAPS input. This converts uppercase word runs (2+
    characters each) to title case so models can still recognise
    proper nouns that appear screaming-upper in the source document.
    Character positions stay the same (only case changes, not length).
    """
    def _to_title(m: re.Match) -> str:
        word = m.group()
        if word.isupper() and len(word) >= 2:
            # U+0130 (LATIN CAPITAL LETTER I WITH DOT ABOVE) lowercases
            # to i + combining dot (U+0307) in Python; strip the
            # combining dot to get plain 'i'.
            lowered = word[1:].lower().replace("i\u0307", "i")
            return word[0] + lowered
        return word

    return _UPPER_WORD_RE.sub(_to_title, text)


# FUTURE: extend Presidio to run with the detected document language once
# per-language SpaCy models are registered.  Until then, the custom regex-
# and validator-based recognizers all declare supported_language="en", so
# Presidio always operates with this default.
_PRESIDIO_DEFAULT_LANGUAGE = "en"

# Septum entity type names may differ from Presidio built-in recognizer names
# (e.g. CREDIT_CARD_NUMBER vs CREDIT_CARD). This mapping bridges the two so
# Presidio built-ins activate automatically. Add new aliases here when a
# name mismatch is discovered.
_PRESIDIO_ENTITY_ALIASES: dict[str, str] = {
    "CREDIT_CARD_NUMBER": "CREDIT_CARD",
    "IBAN": "IBAN_CODE",
}
_PRESIDIO_REVERSE_ALIASES: dict[str, str] = {
    v: k for k, v in _PRESIDIO_ENTITY_ALIASES.items()
}

# Entity types whose detection is delegated to the NER (HuggingFace) and
# semantic layers rather than Presidio pattern recognizers.  Excluded from
# the recognizer coverage validation to avoid noisy warnings.
_NER_LAYER_ENTITY_TYPES: frozenset[str] = frozenset({
    "PERSON_NAME", "FIRST_NAME", "LAST_NAME", "LOCATION", "ORGANIZATION_NAME",
})

# Entity sub-types that are inherently covered when a parent type is
# detected. For example, any PERSON_NAME detection also satisfies
# FIRST_NAME and LAST_NAME requirements from regulations.
_PARENT_TYPE_COVERAGE: dict[str, str] = {
    "FIRST_NAME": "PERSON_NAME",
    "LAST_NAME": "PERSON_NAME",
    "CITY": "LOCATION",
    "STREET_ADDRESS": "POSTAL_ADDRESS",
    "BANK_ACCOUNT_NUMBER": "IBAN",
    "FINANCIAL_ACCOUNT": "CREDIT_CARD_NUMBER",
}

_CONTEXTUAL_ENTITY_TYPES: frozenset[str] = frozenset({
    "DIAGNOSIS", "MEDICATION", "CLINICAL_NOTE",
    "POLITICAL_OPINION", "RELIGION", "ETHNICITY", "SEXUAL_ORIENTATION",
    "BIOMETRIC_ID", "DNA_PROFILE",
})


class BaseCustomRecognizer(EntityRecognizer):
    """Base class for Septum's custom Presidio recognizers.

    Provides a shared entity-filter guard so that subclasses do not need to
    duplicate the ``entities`` intersection check in every ``analyze`` method.
    Subclasses override ``_relevant_entity_types`` if the set of entity types
    to check differs from ``self.supported_entities`` (e.g. address recognizers
    that respond to multiple related types).
    """

    @property
    def _relevant_entity_types(self) -> set[str]:
        """Return entity types that this recognizer responds to.

        Defaults to ``self.supported_entities``. Override in subclasses that
        should also respond to related entity types.
        """
        return set(self.supported_entities)

    def _entity_filter(self, entities: Optional[List[str]]) -> bool:
        """Return True when the recognizer should skip analysis.

        The recognizer should skip when *entities* is explicitly provided and
        none of the requested types overlap with this recognizer's relevant
        entity types.
        """
        if entities and not set(entities).intersection(self._relevant_entity_types):
            return True
        return False


class ExtendedPhoneRecognizer(BaseCustomRecognizer):
    """Presidio recognizer for phone numbers with international format support.

    Phone patterns share a lot of surface area with numeric national
    IDs (Japan My Number, Saudi Iqama, SA ID, Brazilian CPF, …), so a
    loose ``\\d{10,13}`` regex silently eats real identifiers and
    mis-labels them as PHONE_NUMBER. To avoid that, this recognizer
    requires one of the following:

    * an explicit ``+`` international prefix, or
    * at least three digit groups separated by space / dash / dot
      (e.g. ``0505 182 37 96`` or ``(555) 123-4567`` — a bare
      contiguous ``701040108923`` is rejected).

    Span length is capped at 9-15 digits to prevent the optional
    international prefix from greedily consuming the first digits of
    an 11-13 digit identifier.
    """

    def __init__(self) -> None:
        super().__init__(
            supported_entities=["PHONE_NUMBER"],
            supported_language="en",
        )
        # Mode 1 — explicit ``+`` international prefix.
        # Mode 2 — 3+ separator-delimited digit groups, optional
        # parenthesised area code.
        self._pattern = re.compile(
            r"(?:"
            r"\+\d{1,3}[\s\-.]*\(?\d{1,4}\)?(?:[\s\-.]*\d{2,4}){1,4}"
            r"|"
            r"\(?\b\d{3,4}\)?[\s\-.]\d{3,4}[\s\-.]\d{2,4}(?:[\s\-.]\d{2,4})?\b"
            r")"
        )

    def analyze(  # type: ignore[override]
        self,
        text: str,
        entities: Optional[List[str]] = None,
        nlp_artifacts: Optional[object] = None,
    ) -> List[RecognizerResult]:
        if self._entity_filter(entities):
            return []

        results: List[RecognizerResult] = []
        for match in self._pattern.finditer(text):
            span_text = match.group(0)
            digit_count = sum(1 for c in span_text if c.isdigit())
            if not (9 <= digit_count <= 15):
                continue
            start, end = match.span()
            results.append(
                RecognizerResult(
                    entity_type="PHONE_NUMBER",
                    start=start,
                    end=end,
                    score=0.8,
                )
            )
        return results


class ValidatedIBANRecognizer(BaseCustomRecognizer):
    """Presidio recognizer for IBAN values with a format-only fallback.

    Two detection tiers:

    - Primary: ISO 7064 MOD 97-10 checksum. A passing value is an actual
      IBAN and is emitted at high confidence (``0.96``).
    - Fallback: a structurally-shaped IBAN that fails the checksum
      (synthetic data, mis-typed digit, OCR noise). Still emitted as
      IBAN but at a lower score (``0.65``) so downstream filters can
      weigh it accordingly. Privacy-first: a mis-typed IBAN is still
      account information and should not leak to a cloud LLM.

    The compiled pattern accepts both the contiguous form
    (``DE89220041010054014200``) and the paper / statement form with
    4-character groups separated by spaces (``DE89 2004 1010 0540 1420
    00``).  ``BaseIDValidator._normalize`` strips whitespace and common
    separators before checksum evaluation, so a single match path feeds
    both tiers.
    """

    def __init__(self, validator: Optional[IBANValidator] = None) -> None:
        super().__init__(
            supported_entities=["IBAN"],
            supported_language="en",
        )
        self._validator = validator or IBANValidator()
        # 2 letters + 2 digits + 11-32 chars of alphanumerics and spaces.
        # The post-strip length check below narrows this to the 15-34
        # total-char IBAN window mandated by ISO 13616.
        self._pattern = re.compile(
            r"\b[A-Za-z]{2}\d{2}[\s0-9A-Za-z]{11,32}"
        )

    def analyze(  # type: ignore[override]
        self,
        text: str,
        entities: Optional[List[str]] = None,
        nlp_artifacts: Optional[object] = None,
    ) -> List[RecognizerResult]:
        if self._entity_filter(entities):
            return []

        results: List[RecognizerResult] = []
        for match in self._pattern.finditer(text):
            candidate = match.group(0).rstrip()
            stripped = re.sub(r"[\s.-]", "", candidate)
            if not (15 <= len(stripped) <= 34):
                continue
            start = match.start()
            end = start + len(candidate)
            score = 0.96 if self._validator.validate(candidate) else 0.65
            results.append(
                RecognizerResult(
                    entity_type="IBAN",
                    start=start,
                    end=end,
                    score=score,
                )
            )
        return results


class HeuristicPersonNameRecognizer(BaseCustomRecognizer):
    """Lightweight, language-agnostic recognizer for person-like names.

    This recognizer is intentionally heuristic and does not depend on large
    language-specific models. It targets sequences of at least two tokens
    composed of letters (no digits or underscores), starting with an uppercase
    character. This pattern is common for personal names in many scripts and
    languages and works as a safety net when full NER models are unavailable.
    """

    def __init__(self) -> None:
        super().__init__(
            supported_entities=["PERSON_NAME"],
            supported_language="en",
        )
        # Match single capitalized words (2-20 chars, letter-only) that appear
        # alone on a line immediately after a colon. Very narrow pattern to
        # avoid false positives. Language-agnostic.
        self._pattern_after_label = re.compile(
            r":\s*\n\s*([^\W\d_]{2,20})\s*(?:\n|$)",
            re.UNICODE,
        )

    def analyze(  # type: ignore[override]
        self,
        text: str,
        entities: Optional[List[str]] = None,
        nlp_artifacts: Optional[object] = None,
    ) -> List[RecognizerResult]:
        if self._entity_filter(entities):
            return []

        results: List[RecognizerResult] = []
        seen_spans: set[tuple[int, int]] = set()

        for match in self._pattern_after_label.finditer(text):
            group_start = match.start(1)
            group_end = match.end(1)
            matched_text = text[group_start:group_end]

            if not matched_text or not matched_text[0].isupper():
                continue

            key = (group_start, group_end)
            if key not in seen_spans:
                seen_spans.add(key)
                results.append(
                    RecognizerResult(
                        entity_type="PERSON_NAME",
                        start=group_start,
                        end=group_end,
                        score=0.8,
                    )
                )

        return results


class StructuralAddressRecognizer(BaseCustomRecognizer):
    """Presidio recognizer for structured postal addresses.

    Uses two purely structural strategies that work across languages:
    1. Labeled fields — any ``Label : <value>`` line where the value contains
       numeric building/unit references (``NO:5``, ``D:3``, digit sequences
       separated by slashes or dashes).
    2. Dense numeric lines — lines with multiple ``ABBR + number`` or
       ``ABBR + punctuation + number`` tokens (e.g. ``X. 428 Y. Z NO: 10``).

    Abbreviation lists are loaded from the DB at init time when available;
    a small built-in fallback covers common postal abbreviations.
    """

    # FUTURE: move to DB (AddressAbbreviation table or settings).
    # Minimal set of widely-used postal abbreviations across locales.
    _FALLBACK_ABBREVIATIONS: frozenset[str] = frozenset({
        "NO", "APT", "FLAT", "UNIT", "BLOCK", "BLK", "BLDG",
        "ST", "STR", "AVE", "AV", "BLV", "BLVD",
        "MAH", "MH", "SK", "SOK", "CD", "CAD", "KAT",
    })

    def __init__(
        self,
        abbreviations: frozenset[str] | None = None,
    ) -> None:
        super().__init__(
            supported_entities=["POSTAL_ADDRESS"],
            supported_language="en",
        )
        abbrs = abbreviations or self._FALLBACK_ABBREVIATIONS
        abbr_pattern = "|".join(re.escape(a) for a in sorted(abbrs, key=len, reverse=True))

        self._structural_cue = re.compile(
            rf"(?:(?:{abbr_pattern})\b\.?)"
            r"|"
            r"(?:[A-Za-z]+\s*[:./]\s*\d+)",
            re.IGNORECASE | re.UNICODE,
        )
        self._numeric_density = re.compile(
            r"\d+",
        )
        self._label_value = re.compile(
            r"^(\S[^:\n]{1,40})\s*:\s*(.+)$",
            re.MULTILINE | re.UNICODE,
        )

    @property
    def _relevant_entity_types(self) -> set[str]:
        return {"POSTAL_ADDRESS", "STREET_ADDRESS", "LOCATION"}

    def analyze(  # type: ignore[override]
        self,
        text: str,
        entities: Optional[List[str]] = None,
        nlp_artifacts: Optional[object] = None,
    ) -> List[RecognizerResult]:
        if self._entity_filter(entities):
            return []

        results: List[RecognizerResult] = []

        # Strategy 1: labeled fields — ``Label : value`` where value looks
        # like an address (contains multiple structural cues or numbers).
        for label_match in self._label_value.finditer(text):
            value = label_match.group(2).strip()
            cue_count = len(self._structural_cue.findall(value))
            num_count = len(self._numeric_density.findall(value))
            if cue_count >= 2 or (cue_count >= 1 and num_count >= 2):
                val_start = label_match.start(2)
                val_end = label_match.end(2)
                if len(value) >= 10:
                    results.append(
                        RecognizerResult(
                            entity_type="POSTAL_ADDRESS",
                            start=val_start,
                            end=val_end,
                            score=0.82,
                        )
                    )

        # Strategy 2: dense structural lines — lines with 2+ cues that also
        # contain numeric tokens (building numbers, postal codes, etc.).
        for line_match in re.finditer(r"[^\n]+", text):
            line = line_match.group()
            structural_hits = len(self._structural_cue.findall(line))
            numeric_hits = len(self._numeric_density.findall(line))
            if structural_hits >= 2 and numeric_hits >= 1:
                line_start = line_match.start()
                line_end = line_match.end()
                already_covered = any(
                    r.start <= line_start and r.end >= line_end for r in results
                )
                if not already_covered:
                    results.append(
                        RecognizerResult(
                            entity_type="POSTAL_ADDRESS",
                            start=line_start,
                            end=line_end,
                            score=0.78,
                        )
                    )

        return results


class CreditCardNumberRecognizer(BaseCustomRecognizer):
    """Presidio recognizer for credit card numbers.

    Detects numbers matching major card network formats (Visa, Mastercard,
    Amex, Discover, JCB, UnionPay, etc.) without requiring Luhn
    checksum validation. In a privacy-first pipeline, over-detection is
    preferred over missed PII — even a mistyped card number is sensitive.
    """

    def __init__(self) -> None:
        super().__init__(
            supported_entities=["CREDIT_CARD_NUMBER"],
            supported_language="en",
        )
        # Covers major card networks via leading-digit prefixes:
        #   4xxx          — Visa
        #   5[1-5]xx      — Mastercard
        #   2[2-7]xx      — Mastercard (2-series)
        #   3[47]xx       — Amex
        #   3[0689]xx     — Diners Club / Carte Blanche
        #   6xxx          — Discover / UnionPay / misc
        #   35xx          — JCB
        # Allows spaces or dashes as group separators.
        self._pattern = re.compile(
            r"\b"
            r"(?:4\d{3}|5[1-5]\d{2}|2[2-7]\d{2}|3[4-9]\d{2}|3[0-3]\d{2}"
            r"|6(?:011|5\d{2}|4[4-9]\d|22[1-9]|\d{3})"
            r"|35\d{2})"
            r"[\s\-]?\d{3,4}[\s\-]?\d{3,4}[\s\-]?\d{3,5}"
            r"\b"
        )

    def analyze(  # type: ignore[override]
        self,
        text: str,
        entities: Optional[List[str]] = None,
        nlp_artifacts: Optional[object] = None,
    ) -> List[RecognizerResult]:
        if self._entity_filter(entities):
            return []

        results: List[RecognizerResult] = []
        for match in self._pattern.finditer(text):
            digits = re.sub(r"[\s\-]", "", match.group(0))
            if not (13 <= len(digits) <= 19) or not digits.isdigit():
                continue
            start, end = match.span()
            results.append(
                RecognizerResult(
                    entity_type="CREDIT_CARD_NUMBER",
                    start=start,
                    end=end,
                    score=0.85,
                )
            )
        return results


class DateOfBirthRecognizer(BaseCustomRecognizer):
    """Detects dates preceded by birth-related context labels.

    Matches both numeric date formats (``DD/MM/YYYY``, ``YYYY-MM-DD``,
    ``DD.MM.YYYY``) and natural-language "day + month-name + year"
    formats (``22. Juli 1991``, ``09 février 1987``, ``12 de março de
    1983``, ``01 de octubre de 2024``, ``August 15, 1980``) when a
    contextual keyword appears within 40 characters before the date.
    Context keywords are language-agnostic abbreviations and common
    labels.
    """

    # FUTURE: move to DB so admins can extend month vocabularies
    # without code changes. List is kept minimal and covers the
    # Western-European languages we currently benchmark.
    _MONTH_NAMES: tuple[str, ...] = (
        "january", "february", "march", "april", "may", "june",
        "july", "august", "september", "october", "november", "december",
        "januar", "februar", "märz", "maerz", "april", "mai", "juni",
        "juli", "august", "september", "oktober", "november", "dezember",
        "janvier", "février", "fevrier", "mars", "avril", "mai", "juin",
        "juillet", "août", "aout", "septembre", "octobre", "novembre", "décembre", "decembre",
        "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
        "janeiro", "fevereiro", "março", "marco", "abril", "maio", "junho",
        "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
        "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
        "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre",
        "ocak", "şubat", "subat", "mart", "nisan", "mayıs", "mayis", "haziran",
        "temmuz", "ağustos", "agustos", "eylül", "eylul", "ekim", "kasım", "kasim", "aralık", "aralik",
    )

    def __init__(self) -> None:
        super().__init__(
            supported_entities=["DATE_OF_BIRTH"],
            supported_language="en",
        )
        month_alt = "|".join(
            sorted({re.escape(name) for name in self._MONTH_NAMES}, key=len, reverse=True)
        )
        self._date_pattern = re.compile(
            r"(?:"
            r"\b\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}\b"
            r"|\b\d{4}[/.\-]\d{1,2}[/.\-]\d{1,2}\b"
            rf"|\b\d{{1,2}}\.?\s+(?:de\s+)?(?:{month_alt})\s+(?:de\s+)?\d{{2,4}}\b"
            rf"|\b(?:{month_alt})\s+\d{{1,2}}(?:,)?\s+\d{{2,4}}\b"
            r")",
            re.IGNORECASE | re.UNICODE,
        )
        # FUTURE: move context keywords to DB so new locales can be
        # enrolled without editing production code.
        self._context_pattern = re.compile(
            r"(?:d\.?o\.?b\.?|birth\s*date|date\s*of\s*birth"
            r"|born\s*(?:on)?|naissance|nacimiento|nascimento"
            r"|geburtsdatum|geboortedatum|data\s*di\s*nascita"
            r"|do[gğ]um\s*tarihi|fecha\s*de\s*nacimiento"
            r"|data\s*de\s*nascimento|дата\s*рождения"
            r"|تاريخ\s*الميلاد|出生日期|生年月日|जन्म\s*तिथि)",
            re.IGNORECASE,
        )

    def analyze(self, text: str, entities: Optional[List[str]] = None,
                nlp_artifacts: Optional[object] = None) -> List[RecognizerResult]:
        if self._entity_filter(entities):
            return []
        results: List[RecognizerResult] = []
        for m in self._date_pattern.finditer(text):
            window_start = max(0, m.start() - 40)
            window = text[window_start:m.start()]
            if self._context_pattern.search(window):
                results.append(RecognizerResult(
                    entity_type="DATE_OF_BIRTH", start=m.start(), end=m.end(), score=0.96,
                ))
        return results


class MACAddressRecognizer(BaseCustomRecognizer):
    """Detects MAC addresses in colon, dash, or dot notation."""

    def __init__(self) -> None:
        super().__init__(
            supported_entities=["MAC_ADDRESS"],
            supported_language="en",
        )
        self._pattern = re.compile(
            r"\b(?:[0-9A-Fa-f]{2}[:\-]){5}[0-9A-Fa-f]{2}\b"
            r"|\b(?:[0-9A-Fa-f]{4}\.){2}[0-9A-Fa-f]{4}\b"
        )

    def analyze(self, text: str, entities: Optional[List[str]] = None,
                nlp_artifacts: Optional[object] = None) -> List[RecognizerResult]:
        if self._entity_filter(entities):
            return []
        return [RecognizerResult(entity_type="MAC_ADDRESS", start=m.start(), end=m.end(), score=0.90)
                for m in self._pattern.finditer(text)]


class URLRecognizer(BaseCustomRecognizer):
    """Detects HTTP/HTTPS URLs."""

    def __init__(self) -> None:
        super().__init__(
            supported_entities=["URL"],
            supported_language="en",
        )
        self._pattern = re.compile(
            r"https?://[^\s<>\"')\]},;]+",
            re.IGNORECASE,
        )

    def analyze(self, text: str, entities: Optional[List[str]] = None,
                nlp_artifacts: Optional[object] = None) -> List[RecognizerResult]:
        if self._entity_filter(entities):
            return []
        return [RecognizerResult(entity_type="URL", start=m.start(), end=m.end(), score=0.90)
                for m in self._pattern.finditer(text)]


class CoordinatesRecognizer(BaseCustomRecognizer):
    """Detects geographic coordinates in decimal degrees or DMS notation."""

    def __init__(self) -> None:
        super().__init__(
            supported_entities=["COORDINATES"],
            supported_language="en",
        )
        self._decimal_pattern = re.compile(
            r"-?\d{1,3}\.\d{3,8}\s*[°]?\s*[NSns]?\s*[,/;\s]\s*-?\d{1,3}\.\d{3,8}\s*[°]?\s*[EWew]?"
        )
        self._dms_pattern = re.compile(
            r"\d{1,3}°\s*\d{1,2}[′']\s*\d{1,2}(?:\.\d+)?[″\"]\s*[NSns]\s*"
            r"\d{1,3}°\s*\d{1,2}[′']\s*\d{1,2}(?:\.\d+)?[″\"]\s*[EWew]"
        )

    def analyze(self, text: str, entities: Optional[List[str]] = None,
                nlp_artifacts: Optional[object] = None) -> List[RecognizerResult]:
        if self._entity_filter(entities):
            return []
        results: List[RecognizerResult] = []
        for pat in (self._decimal_pattern, self._dms_pattern):
            for m in pat.finditer(text):
                results.append(RecognizerResult(
                    entity_type="COORDINATES", start=m.start(), end=m.end(), score=0.85,
                ))
        return results


class CookieIDRecognizer(BaseCustomRecognizer):
    """Detects tracking cookie identifiers (Google Analytics, UUID-based, etc.)."""

    def __init__(self) -> None:
        super().__init__(
            supported_entities=["COOKIE_ID"],
            supported_language="en",
        )
        self._ga_pattern = re.compile(r"_ga\s*=\s*GA\d\.\d+\.\d+\.\d+")
        self._cookie_uuid_pattern = re.compile(
            r"(?:cookie|session[_\-]?id|tracking[_\-]?id)\s*[=:]\s*"
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            re.IGNORECASE,
        )

    def analyze(self, text: str, entities: Optional[List[str]] = None,
                nlp_artifacts: Optional[object] = None) -> List[RecognizerResult]:
        if self._entity_filter(entities):
            return []
        results: List[RecognizerResult] = []
        for pat in (self._ga_pattern, self._cookie_uuid_pattern):
            for m in pat.finditer(text):
                results.append(RecognizerResult(
                    entity_type="COOKIE_ID", start=m.start(), end=m.end(), score=0.88,
                ))
        return results


class DeviceIDRecognizer(BaseCustomRecognizer):
    """Detects device identifiers (IMEI, Android ID, UUID-based device IDs)."""

    def __init__(self) -> None:
        super().__init__(
            supported_entities=["DEVICE_ID"],
            supported_language="en",
        )
        self._imei_pattern = re.compile(
            r"(?:IMEI|imei)\s*[=:]\s*\d{15,17}"
        )
        self._device_uuid_pattern = re.compile(
            r"(?:device[_\-]?id|android[_\-]?id|udid|idfa|idfv|gaid)\s*[=:]\s*"
            r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}",
            re.IGNORECASE,
        )
        self._serial_pattern = re.compile(
            r"(?:serial\s*(?:number|no|#)?|s/n)\s*[=:]\s*[A-Za-z0-9]{8,20}",
            re.IGNORECASE,
        )

    def analyze(self, text: str, entities: Optional[List[str]] = None,
                nlp_artifacts: Optional[object] = None) -> List[RecognizerResult]:
        if self._entity_filter(entities):
            return []
        results: List[RecognizerResult] = []
        for pat in (self._imei_pattern, self._device_uuid_pattern, self._serial_pattern):
            for m in pat.finditer(text):
                results.append(RecognizerResult(
                    entity_type="DEVICE_ID", start=m.start(), end=m.end(), score=0.85,
                ))
        return results


class SSNRecognizer(BaseCustomRecognizer):
    """Detects Social Security Numbers with context keywords.

    Matches only when a ``SSN`` / ``social security`` context keyword
    appears within 40 characters before the candidate. A checksum-valid
    SSN is emitted at ``0.92``; a synthetic or typo'd SSN that still
    sits in the correct format after the context gate is kept at a
    reduced ``0.55`` so the raw digits never leak through unmasked.
    """

    def __init__(self) -> None:
        super().__init__(
            supported_entities=["SOCIAL_SECURITY_NUMBER"],
            supported_language="en",
        )
        self._pattern = re.compile(r"\b\d{3}[\s\-]?\d{2}[\s\-]?\d{4}\b")
        # FUTURE: move context keywords to DB so regulation packs can
        # register locale-specific preamble vocabulary without code edits.
        self._context = re.compile(
            r"(?:SSN|social\s*security)",
            re.IGNORECASE,
        )

    def analyze(self, text: str, entities: Optional[List[str]] = None,
                nlp_artifacts: Optional[object] = None) -> List[RecognizerResult]:
        if self._entity_filter(entities):
            return []
        from .national_ids import SSNValidator
        validator = SSNValidator()
        results: List[RecognizerResult] = []
        for m in self._pattern.finditer(text):
            window_start = max(0, m.start() - 40)
            window = text[window_start:m.start()]
            if not self._context.search(window):
                continue
            digits = re.sub(r"[\s\-]", "", m.group())
            score = 0.92 if validator.validate(digits) else 0.55
            results.append(RecognizerResult(
                entity_type="SOCIAL_SECURITY_NUMBER", start=m.start(), end=m.end(), score=score,
            ))
        return results


class CPFRecognizer(BaseCustomRecognizer):
    """Detects CPF numbers with algorithmic checksum validation.

    A checksum-valid CPF is emitted at ``0.95`` without needing any
    context. A 11-digit dotted candidate that fails the checksum is
    still kept at ``0.55`` when a ``CPF`` context keyword sits within
    40 characters before the candidate — the context gate is the
    safety net against random 11-digit dotted sequences slipping
    through as CPF.
    """

    def __init__(self) -> None:
        super().__init__(
            supported_entities=["CPF"],
            supported_language="en",
        )
        self._pattern = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}[\-.]?\d{2}\b")
        self._context = re.compile(r"\bcpf\b", re.IGNORECASE)

    def analyze(self, text: str, entities: Optional[List[str]] = None,
                nlp_artifacts: Optional[object] = None) -> List[RecognizerResult]:
        if self._entity_filter(entities):
            return []
        from .national_ids import CPFValidator
        validator = CPFValidator()
        results: List[RecognizerResult] = []
        for m in self._pattern.finditer(text):
            digits = re.sub(r"[.\-]", "", m.group())
            if validator.validate(digits):
                score = 0.95
            else:
                window = text[max(0, m.start() - 40): m.start()]
                if not self._context.search(window):
                    continue
                score = 0.55
            results.append(RecognizerResult(
                entity_type="CPF", start=m.start(), end=m.end(), score=score,
            ))
        return results


class PassportNumberRecognizer(BaseCustomRecognizer):
    """Detects passport numbers with context keywords.

    Passport formats vary by country but share common structural patterns:
    1-2 letters followed by 6-9 digits, or purely numeric (9 digits).
    Requires a contextual keyword to reduce false positives.
    """

    def __init__(self) -> None:
        super().__init__(
            supported_entities=["PASSPORT_NUMBER"],
            supported_language="en",
        )
        self._pattern = re.compile(
            r"\b[A-Z]{0,2}\d{6,9}\b"
        )
        # FUTURE: move context keywords to DB so each regulation pack can
        # register its own locale-specific preamble vocabulary. The list
        # below is a multilingual fallback kept equal across locales.
        self._context = re.compile(
            r"(?:passport|pasaport|reisepass|passeport|passaporto|paspoort"
            r"|паспорт|جواز\s*سفر|护照|パスポート|पासपोर्ट)",
            re.IGNORECASE,
        )

    def analyze(self, text: str, entities: Optional[List[str]] = None,
                nlp_artifacts: Optional[object] = None) -> List[RecognizerResult]:
        if self._entity_filter(entities):
            return []
        results: List[RecognizerResult] = []
        for m in self._pattern.finditer(text):
            window_start = max(0, m.start() - 50)
            window = text[window_start:m.start()]
            if self._context.search(window):
                results.append(RecognizerResult(
                    entity_type="PASSPORT_NUMBER", start=m.start(), end=m.end(), score=0.88,
                ))
        return results


class DriversLicenseRecognizer(BaseCustomRecognizer):
    """Detects driver's license numbers with context keywords.

    Formats vary widely; requires contextual keyword to reduce false positives.
    """

    def __init__(self) -> None:
        super().__init__(
            supported_entities=["DRIVERS_LICENSE"],
            supported_language="en",
        )
        self._pattern = re.compile(r"\b[A-Z0-9]{5,15}\b")
        # FUTURE: move context keywords to DB so each regulation pack can
        # register its own locale-specific preamble vocabulary.
        self._context = re.compile(
            r"(?:driver'?s?\s*licen[sc]e|driving\s*licen[sc]e|DL\s*#?|ehliyet"
            r"|permis\s*de\s*conduire|führerschein|rijbewijs|patente\s*di\s*guida"
            r"|licencia\s*de\s*conducir|carta\s*de\s*condução"
            r"|водительское\s*удостоверение)",
            re.IGNORECASE,
        )

    def analyze(self, text: str, entities: Optional[List[str]] = None,
                nlp_artifacts: Optional[object] = None) -> List[RecognizerResult]:
        if self._entity_filter(entities):
            return []
        results: List[RecognizerResult] = []
        for m in self._pattern.finditer(text):
            window_start = max(0, m.start() - 50)
            window = text[window_start:m.start()]
            if self._context.search(window):
                val = m.group()
                if len(val) >= 5 and any(c.isdigit() for c in val):
                    results.append(RecognizerResult(
                        entity_type="DRIVERS_LICENSE", start=m.start(), end=m.end(), score=0.85,
                    ))
        return results


class TaxIDRecognizer(BaseCustomRecognizer):
    """Detects tax identification numbers with context keywords.

    Matches a number of structural families preceded by a tax-related
    context keyword. The patterns intentionally overlap so that
    space/dot/slash variants and the optional 2-letter country prefix
    used by EU VAT identifiers all resolve to the same TAX_ID entity:

    * ``12-3456789`` / ``12 3456789`` (US-style with 2-digit prefix)
    * ``27/431/07892`` (German Steuernummer — slashes)
    * ``DE298431076`` / ``FR 47 834291076`` (EU VAT with country prefix)
    * ``B-86432178`` / ``B86432178`` (Spanish CIF — single letter prefix)
    * ``123.456.789.112`` / ``12.345.678-9`` (dotted formats used by
      Brazilian Inscrição Estadual and Latin-American TINs)
    """

    def __init__(self) -> None:
        super().__init__(
            supported_entities=["TAX_ID"],
            supported_language="en",
        )
        self._pattern = re.compile(
            r"(?:"
            r"\b[A-Z]{2}\s?\d{2}\s?\d{6,10}\b"
            r"|\b[A-Z]-?\d{7,8}[A-Z0-9]?\b"
            r"|\b\d{2,3}/\d{3,4}/\d{4,5}\b"
            r"|\b\d{2,3}(?:\.\d{3}){2,3}(?:[-.]\d{1,2})?\b"
            r"|\b\d{2}\s\d{3}\s\d{3}\s\d{3}\b"
            r"|\b\d{2}[\-]?\d{7,10}\b"
            r")",
            re.IGNORECASE,
        )
        # FUTURE: move context keywords to DB so each regulation pack can
        # register its own locale-specific preamble vocabulary.
        self._context = re.compile(
            r"(?:tax\s*id|TIN|EIN|vergi\s*(?:no|numarası|kimlik)"
            r"|steuer[\-\s]?(?:nummer|id)|ust[\-\s]?id|USt[\-\s]?IdNr"
            r"|NIF|CIF|NIE|N(?:º|[oO]\.?)\s*SIREN|SIRET|tva\s*intracommunautaire"
            r"|codice\s*fiscale|partita\s*iva|numéro\s*fiscal"
            r"|inscrição\s*estadual|CNPJ|CPF"
            r"|ИНН|الرقم\s*الضريبي|税号)",
            re.IGNORECASE,
        )

    def analyze(self, text: str, entities: Optional[List[str]] = None,
                nlp_artifacts: Optional[object] = None) -> List[RecognizerResult]:
        if self._entity_filter(entities):
            return []
        results: List[RecognizerResult] = []
        for m in self._pattern.finditer(text):
            window_start = max(0, m.start() - 40)
            window = text[window_start:m.start()]
            if self._context.search(window):
                results.append(RecognizerResult(
                    entity_type="TAX_ID", start=m.start(), end=m.end(), score=0.85,
                ))
        return results


class LicensePlateRecognizer(BaseCustomRecognizer):
    """Detects vehicle license plate numbers with context keywords.

    Matches common alphanumeric plate patterns when preceded by
    vehicle/plate context to avoid false positives on generic codes.
    """

    def __init__(self) -> None:
        super().__init__(
            supported_entities=["LICENSE_PLATE"],
            supported_language="en",
        )
        # Two structural plate families: "<digits><letters><digits>" and
        # "<letters><digits><optional letters>". Not tied to any locale.
        self._pattern = re.compile(
            r"\b\d{2}\s*[A-Z]{1,3}\s*\d{2,4}\b"
            r"|\b[A-Z]{1,3}[\s\-]?\d{1,4}[\s\-]?[A-Z]{0,3}\b"
        )
        # FUTURE: move context keywords to DB so each regulation pack can
        # register its own locale-specific preamble vocabulary.
        self._context = re.compile(
            r"(?:licen[sc]e\s*plate|plate\s*(?:no|number)|plaka|vehicle\s*reg"
            r"|kennzeichen|immatriculation|targa|matrícula|kenteken"
            r"|номерной\s*знак|لوحة\s*السيارة|车牌|ナンバープレート)",
            re.IGNORECASE,
        )

    def analyze(self, text: str, entities: Optional[List[str]] = None,
                nlp_artifacts: Optional[object] = None) -> List[RecognizerResult]:
        if self._entity_filter(entities):
            return []
        results: List[RecognizerResult] = []
        for m in self._pattern.finditer(text):
            window_start = max(0, m.start() - 40)
            window = text[window_start:m.start()]
            if self._context.search(window):
                results.append(RecognizerResult(
                    entity_type="LICENSE_PLATE", start=m.start(), end=m.end(), score=0.84,
                ))
        return results


class Detector:
    """High-level orchestrator for multi-layer PII detection and masking.

    Pipeline layers:

    1. Presidio AnalyzerEngine with custom recognizers.
    2. HuggingFace NER transformer models via :class:`NERModelRegistry`.
    3. Optional semantic validation via the attached
       :class:`SemanticDetectionPort` (filters false positives).
    4. Optional semantic alias / contextual / coreference layers via
       the same port.

    All semantic / LLM plumbing lives on the caller side inside an
    adapter that implements :class:`SemanticDetectionPort`; core keeps
    a :class:`NullSemanticDetectionPort` as the default so non-network
    environments can run the regex/NER stack with zero wiring.
    """

    def __init__(
        self,
        config: SeptumCoreConfig,
        ner_registry: Optional[NERModelRegistry] = None,
        policy: Optional[ComposedPolicy] = None,
        semantic_port: Optional[SemanticDetectionPort] = None,
    ) -> None:
        self._config = config
        self._ner_registry = ner_registry or NERModelRegistry()
        self._semantic_port: SemanticDetectionPort = (
            semantic_port or NullSemanticDetectionPort()
        )
        self._analyzer = AnalyzerEngine(nlp_engine=_get_shared_nlp_engine())
        self._entity_types: Optional[List[str]] = None
        self._non_pii_filter: Optional[NonPiiFilter] = None
        self._register_custom_recognizers()
        if policy is not None:
            self._apply_policy(policy)

    def _apply_policy(self, policy: ComposedPolicy) -> None:
        """
        Configure the Presidio analyzer with recognizers from the composed policy.

        Regulation-aware recognizers from ``policy.recognizers`` are registered
        in addition to the project-specific baseline recognizers defined in
        this module. The union of entity types from the policy is stored so
        that ``sanitize`` can limit detection to the active set.

        After applying the policy, runs a coverage validation that logs
        warnings for entity types with no matching Presidio recognizer.
        """
        registry = self._analyzer.registry
        for recognizer in policy.recognizers:
            registry.add_recognizer(recognizer)
        self._entity_types = list(policy.entity_types)
        self._non_pii_filter = NonPiiFilter.from_rules(policy.non_pii_rules)
        self._validate_entity_coverage()

    def _validate_entity_coverage(self) -> None:
        """Log warnings for policy entity types with no matching recognizer.

        Checks every entity type required by active regulations against the
        set of entity types supported by registered Presidio recognizers
        (including aliases).  Types covered by NER or contextual layers are
        excluded from the check.
        """
        if not self._entity_types:
            return

        supported: set[str] = set()
        try:
            recognizers = self._analyzer.registry.get_recognizers(
                language=_PRESIDIO_DEFAULT_LANGUAGE, all_fields=True,
            )
            for rec in recognizers:
                supported.update(rec.supported_entities)
        except Exception:
            return

        for septum_type, presidio_type in _PRESIDIO_ENTITY_ALIASES.items():
            if presidio_type in supported:
                supported.add(septum_type)

        skip = _NER_LAYER_ENTITY_TYPES | _CONTEXTUAL_ENTITY_TYPES | frozenset(_PARENT_TYPE_COVERAGE)
        uncovered = [
            et for et in self._entity_types
            if et not in supported and et not in skip
        ]
        for et in uncovered:
            logger.warning(
                "Entity type '%s' is required by active regulations but has no "
                "Presidio recognizer or alias. Detection relies on NER/semantic only.",
                et,
            )

    @staticmethod
    def _expand_with_aliases(
        entity_types: Optional[List[str]],
    ) -> Optional[List[str]]:
        """Expand entity type list with Presidio and parent-coverage aliases.

        The policy may reference a Septum entity type that is actually
        *covered* by a sibling detector — for example a regulation
        declares ``BANK_ACCOUNT_NUMBER`` and we satisfy it with
        ``IBAN``, or the regulation lists ``FIRST_NAME`` but we only
        run a single ``PERSON_NAME`` detector. This helper returns a
        new list that contains:

        1. the original types,
        2. each type's Presidio built-in alias (``CREDIT_CARD_NUMBER``
           → ``CREDIT_CARD``, ``IBAN`` → ``IBAN_CODE``),
        3. each type's ``_PARENT_TYPE_COVERAGE`` parent (e.g.
           ``BANK_ACCOUNT_NUMBER`` → ``IBAN``).

        Without the parent-coverage expansion, Presidio's entity filter
        would hide the IBAN / PERSON_NAME detectors whenever a
        regulation only names the child alias, which is a silent
        detection regression.
        """
        if entity_types is None:
            return None
        expanded = list(entity_types)
        for septum_type in entity_types:
            alias = _PRESIDIO_ENTITY_ALIASES.get(septum_type)
            if alias and alias not in expanded:
                expanded.append(alias)
            parent = _PARENT_TYPE_COVERAGE.get(septum_type)
            if parent and parent not in expanded:
                expanded.append(parent)
        return expanded

    def _register_custom_recognizers(self) -> None:
        """Register project-specific Presidio recognizers on the analyzer registry.

        These are regulation-agnostic, format-driven detectors. Country- or
        language-specific detectors (e.g. checksum-validated national IDs)
        belong to the regulation packs under ``recognizers/<reg_id>/`` and
        are added to the registry via ``_apply_policy``.
        """
        registry = self._analyzer.registry
        registry.add_recognizer(ExtendedPhoneRecognizer())
        registry.add_recognizer(ValidatedIBANRecognizer())
        registry.add_recognizer(HeuristicPersonNameRecognizer())
        registry.add_recognizer(StructuralAddressRecognizer())
        registry.add_recognizer(CreditCardNumberRecognizer())
        registry.add_recognizer(DateOfBirthRecognizer())
        registry.add_recognizer(MACAddressRecognizer())
        registry.add_recognizer(URLRecognizer())
        registry.add_recognizer(CoordinatesRecognizer())
        registry.add_recognizer(CookieIDRecognizer())
        registry.add_recognizer(DeviceIDRecognizer())
        registry.add_recognizer(SSNRecognizer())
        registry.add_recognizer(CPFRecognizer())
        registry.add_recognizer(PassportNumberRecognizer())
        registry.add_recognizer(DriversLicenseRecognizer())
        registry.add_recognizer(TaxIDRecognizer())
        registry.add_recognizer(LicensePlateRecognizer())

    def sanitize(
        self,
        text: str,
        language: str,
        anon_map: AnonymizationMap,
    ) -> SanitizeResult:
        """Run the multi-layer sanitizer on the given text."""
        if not text:
            return SanitizeResult(sanitized_text=text, entity_count=0)

        normalized_text = normalize_unicode(text)
        spans: List[DetectedSpan] = []

        if self._config.use_presidio_layer:
            presidio_entities = self._expand_with_aliases(self._entity_types)
            presidio_results = self._analyzer.analyze(
                text=normalized_text,
                language=_PRESIDIO_DEFAULT_LANGUAGE,
                entities=presidio_entities,
            )
            presidio_results = self._filter_presidio_results(
                presidio_results, normalized_text
            )
            spans.extend(self._from_presidio_results(presidio_results))

        text_len = len(normalized_text.strip())

        if self._config.use_ner_layer and text_len >= _MIN_TEXT_LENGTH_FOR_NER:
            ner_pipeline = self._ner_registry.get_pipeline(language)
            ner_results = ner_pipeline(normalized_text)
            ner_spans = self._from_ner_results(ner_results, normalized_text, language)

            # Re-run NER on title-cased text to catch ALL CAPS names that
            # transformer models miss (trained on mixed-case text).
            titlecased = _titlecase_upper_segments(normalized_text)
            if titlecased != normalized_text:
                tc_results = ner_pipeline(titlecased)
                tc_spans = self._from_ner_results(tc_results, normalized_text, language)
                existing = {(s.start, s.end) for s in ner_spans}
                ner_spans.extend(s for s in tc_spans if (s.start, s.end) not in existing)
            if self._entity_types is not None:
                allowed = set(self._entity_types)
                ner_spans = [s for s in ner_spans if s.entity_type in allowed]
            spans.extend(ner_spans)

        port_enabled = self._semantic_port.is_enabled()

        if self._config.use_semantic_validation and port_enabled and spans:
            try:
                validated_spans = self._semantic_validate_candidates(
                    text=normalized_text,
                    candidate_spans=spans,
                    language=language,
                )
                logger.debug(
                    "Semantic validation: %d candidates → %d validated spans",
                    len(spans),
                    len(validated_spans),
                )
                spans = validated_spans
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "Semantic validation layer failed, continuing with all candidates: %s", e
                )

        if (
            self._config.use_semantic_detection
            and port_enabled
            and text_len >= _MIN_TEXT_LENGTH_FOR_SEMANTIC_ALIAS
        ):
            try:
                semantic_spans = self._semantic_port.detect_aliases(
                    normalized_text=normalized_text,
                )
                logger.debug(
                    "Semantic alias layer returned %d spans",
                    len(semantic_spans),
                )
                spans.extend(semantic_spans)
            except Exception as e:  # noqa: BLE001
                logger.warning("Semantic alias layer failed, continuing without it: %s", e)

            if self._config.use_semantic_contextual_detection:
                semantic_types = [
                    et for et in (self._entity_types or [])
                    if et in _CONTEXTUAL_ENTITY_TYPES
                ]
                if semantic_types:
                    try:
                        contextual_spans = self._semantic_port.detect_semantic(
                            normalized_text=normalized_text,
                            entity_types=semantic_types,
                        )
                        logger.debug(
                            "Semantic contextual layer returned %d spans",
                            len(contextual_spans),
                        )
                        spans.extend(contextual_spans)
                    except Exception as e:  # noqa: BLE001
                        logger.warning("Semantic contextual layer failed: %s", e)

        if self._non_pii_filter is not None:
            span_views = [
                SpanView(
                    start=s.start,
                    end=s.end,
                    entity_type=s.entity_type,
                    score=s.score,
                )
                for s in spans
            ]
            filtered_views = self._non_pii_filter.filter_spans(
                text=normalized_text,
                language=language,
                spans=span_views,
            )
            keep_spans: List[DetectedSpan] = []
            for view in filtered_views:
                for original in spans:
                    if (
                        original.start == view.start
                        and original.end == view.end
                        and original.entity_type == view.entity_type
                        and original.score == view.score
                    ):
                        keep_spans.append(original)
                        break
            spans = keep_spans

        spans = deduplicate_spans(spans, _HIGH_PRIORITY_ENTITY_TYPES)

        # Expand person-name spans to cover adjacent capitalized tokens so that
        # given-name-only detections are upgraded to full name blocks where
        # possible. This is language-agnostic and relies only on character
        # casing and token boundaries.
        spans = expand_person_name_spans(normalized_text, spans)
        spans = merge_adjacent_person_name_spans(normalized_text, spans)

        spans = [
            s
            for s in spans
            if normalize_for_comparison(
                normalized_text[s.start : s.end].strip(), language
            )
            not in SANITIZER_STOPWORDS
        ]

        sanitized, count, resolved_spans = self._apply_replacements(
            normalized_text, spans, anon_map, language
        )

        if (
            self._config.use_semantic_detection
            and port_enabled
            and anon_map.entity_map
        ):
            try:
                self._semantic_port.resolve_coreference(
                    normalized_text=normalized_text,
                    anon_map=anon_map,
                    language=language,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("Pronoun coreference resolution failed, continuing: %s", e)

        sanitized = anon_map.apply_blocklist(sanitized, language)
        self._log_low_confidence(spans)

        type_counts: Dict[str, int] = {}
        for span in spans:
            type_counts[span.entity_type] = type_counts.get(span.entity_type, 0) + 1

        return SanitizeResult(
            sanitized_text=sanitized,
            entity_count=count,
            entity_type_counts=type_counts,
            detected_spans=resolved_spans,
        )

    def _filter_presidio_results(
        self,
        results: List[RecognizerResult],
        text: str,
    ) -> List[RecognizerResult]:
        """
        Filter Presidio results before conversion to DetectedSpan.

        When the NER layer is enabled, generic PERSON_NAME entities from
        Presidio are suppressed so that person detection is driven primarily
        by the language-specific NER models. This reduces false positives on
        ordinary title or heading text while preserving all other Presidio
        detections (for example, phone numbers and validated identifiers).
        """
        if not results:
            return results

        filtered: List[RecognizerResult] = []
        for r in results:

            if self._config.use_ner_layer and r.entity_type == "PERSON_NAME" and r.score < 0.7:
                continue

            if r.entity_type == "PHONE_NUMBER":
                span_text = text[r.start : r.end]
                digit_count = sum(1 for ch in span_text if ch.isdigit())
                has_dot_or_slash = "." in span_text or "/" in span_text
                if has_dot_or_slash and digit_count <= 8:
                    continue

            if r.entity_type == "LOCATION":
                # Mirror _map_ner_label: Presidio's built-in SpacyRecognizer
                # maps spaCy GPE to LOCATION, and with en_core_web_sm
                # running over non-English text it produces stochastic
                # mis-fires on any Title Case OOV token — "Doğum",
                # "Uyruk", "TARAFLAR", "T.C." in Turkish and equivalents
                # in every other language. Address PII is captured by the
                # deterministic StructuralAddressRecognizer and per-regulation
                # POSTAL_ADDRESS / STREET_ADDRESS recognizers, so there is no
                # legitimate LOCATION source left on the Presidio side either.
                continue

            filtered.append(r)
        return filtered

    def _semantic_validate_candidates(
        self,
        text: str,
        candidate_spans: List[DetectedSpan],
        language: str,
    ) -> List[DetectedSpan]:
        """Delegate candidate validation to the attached semantic port.

        Structured identifiers (national ID, IBAN, phone, etc.) are **never**
        sent through the port: empty or malformed LLM responses must not
        strip them (privacy). Only non-passthrough spans are handed over;
        if the port yields no matches, the passthrough set is still returned
        so structural PII never leaks even on adapter failure.
        """
        if not candidate_spans or not text:
            return candidate_spans

        passthrough: List[DetectedSpan] = [
            s for s in candidate_spans if s.entity_type in _SEMANTIC_VALIDATION_PASSTHROUGH_TYPES
        ]
        to_validate: List[DetectedSpan] = [
            s for s in candidate_spans if s.entity_type not in _SEMANTIC_VALIDATION_PASSTHROUGH_TYPES
        ]

        if not to_validate:
            return passthrough

        regulation_context = self._build_regulation_context()

        validated = self._semantic_port.validate_candidates(
            text=text,
            candidate_spans=to_validate,
            language=language,
            regulation_context=regulation_context,
        )

        if not validated:
            logger.warning(
                "Semantic validation matched no spans; keeping original non-passthrough candidates"
            )
            return sorted(
                passthrough + to_validate,
                key=lambda s: s.start,
            )

        return sorted(
            passthrough + validated,
            key=lambda s: s.start,
        )

    def _build_regulation_context(self) -> str:
        """Build a human-readable summary of active regulation rules."""
        if self._entity_types is None or not self._entity_types:
            return "No specific regulations active (generic PII detection)."

        context_parts = [
            "Active entity types for PII detection:",
            ", ".join(sorted(set(self._entity_types))),
        ]
        return "\n".join(context_parts)

    @staticmethod
    def _from_presidio_results(
        results: List[RecognizerResult],
    ) -> List[DetectedSpan]:
        """Convert Presidio RecognizerResult objects to DetectedSpan.

        Entity types returned by Presidio built-in recognizers are mapped back
        to Septum entity type names via ``_PRESIDIO_REVERSE_ALIASES``.
        """
        spans: List[DetectedSpan] = []
        for r in results:
            entity_type = _PRESIDIO_REVERSE_ALIASES.get(r.entity_type, r.entity_type)
            spans.append(
                DetectedSpan(
                    start=r.start,
                    end=r.end,
                    entity_type=entity_type,
                    score=r.score,
                )
            )
        return spans

    @staticmethod
    def _snap_to_word_boundaries(text: str, start: int, end: int) -> tuple[int, int]:
        """Expand a span to cover the full word(s) it touches.

        Subword-tokenisation models can produce spans that begin or end
        mid-word (e.g. "kara" inside "Ankara"). This helper expands the
        boundaries outward to the nearest whitespace / punctuation so the
        replacement never breaks a word.
        """
        _BOUNDARY_CHARS = frozenset(" \t\n\r.,;:!?()[]{}\"'/-")
        while start > 0 and text[start - 1] not in _BOUNDARY_CHARS:
            start -= 1
        n = len(text)
        while end < n and text[end] not in _BOUNDARY_CHARS:
            end += 1
        return start, end

    @staticmethod
    def _from_ner_results(
        raw_results: List[dict],
        text: str,
        language: str = "en",
    ) -> List[DetectedSpan]:
        """Convert HuggingFace NER pipeline outputs to DetectedSpan.

        Applies a uniform confidence threshold of 0.85 for all languages
        to limit false positives. Spans are snapped to word boundaries
        to prevent mid-word replacements caused by subword tokenisation.
        Spans shorter than 3 characters are discarded as unreliable.

        LOCATION entities pass through a conservative multi-word-or-high-
        score gate (same shape as ORGANIZATION_NAME) to drop the locale-
        specific single-token mis-fires that motivated the earlier full
        suppression. See ``_map_ner_label`` for the rationale.
        """
        threshold = 0.85
        min_span_len = 3
        spans: List[DetectedSpan] = []
        for item in raw_results:
            entity = item.get("entity_group") or item.get("entity")
            start = int(item["start"])
            end = int(item["end"])
            score = float(item.get("score", 0.0))
            if not entity:
                continue
            entity_type = Detector._map_ner_label(entity)
            if entity_type is None:
                continue

            if score < threshold:
                continue

            start, end = Detector._snap_to_word_boundaries(text, start, end)

            span_text = text[start:end].strip()
            if len(span_text) < min_span_len:
                continue

            if entity_type == "ORGANIZATION_NAME":
                words = span_text.split()
                if len(words) < 2 and score < 0.95:
                    continue

            if entity_type == "LOCATION":
                # Same conservative filter as ORGANIZATION_NAME: accept only
                # multi-word spans or very high-confidence single tokens.
                # Keeps real placenames ("Paris", "İstanbul" — score > 0.95)
                # while dropping the locale-specific single-token mis-fires
                # (Turkish "Doğum", German form headers, etc.) that poisoned
                # the naive LOC → LOCATION mapping before it was dropped.
                words = span_text.split()
                if len(words) < 2 and score < 0.95:
                    continue

            spans.append(
                DetectedSpan(
                    start=start,
                    end=end,
                    entity_type=entity_type,
                    score=score,
                )
            )
        return spans

    @staticmethod
    def _map_ner_label(label: str) -> Optional[str]:
        """Map model-specific NER labels to global entity types.

        Four labels are mapped: PER → PERSON_NAME, EMAIL → EMAIL_ADDRESS,
        ORG → ORGANIZATION_NAME, LOC/GPE → LOCATION. ORGANIZATION_NAME
        and LOCATION then flow through a conservative confidence filter
        in ``_extract_entities`` (multi-word span OR score > 0.95) —
        multilingual NER models (XLM-RoBERTa variants) are stochastic
        and produce locale-specific single-token false positives on
        every language Septum supports (Turkish "Doğum", German form
        headers, Japanese common nouns). The multi-word-or-high-score
        gate drops those without a language-specific stopword list,
        keeping the zero-tolerance generic-architecture rule intact.

        Real placenames in free text ("Paris", "İstanbul", "New York")
        routinely score above 0.95 on the XLM-RoBERTa variants used,
        and multi-word locations ("New York", "San Francisco") bypass
        the score gate entirely. Structured address PII is still caught
        by ``StructuralAddressRecognizer`` and per-regulation
        POSTAL_ADDRESS / STREET_ADDRESS recognizers —
        ``_extract_entities``'s LOCATION output is additive on top of
        those.
        """
        upper = label.upper()
        if "PER" in upper or upper.startswith("B-PER") or upper.startswith("I-PER"):
            return "PERSON_NAME"
        if "EMAIL" in upper:
            return "EMAIL_ADDRESS"
        if "ORG" in upper or upper.startswith("B-ORG") or upper.startswith("I-ORG"):
            return "ORGANIZATION_NAME"
        if "LOC" in upper or "GPE" in upper:
            return "LOCATION"
        return None

    @staticmethod
    def _apply_replacements(
        text: str,
        spans: List[DetectedSpan],
        anon_map: AnonymizationMap,
        language: str,
    ) -> tuple[str, int, List[ResolvedSpan]]:
        """Replace detected spans with placeholders using the anonymization map."""
        if not spans:
            return text, 0, []

        parts: List[str] = []
        resolved: List[ResolvedSpan] = []
        last_index = 0
        count = 0

        for span in sorted(spans, key=lambda s: s.start):
            if span.start < last_index:
                continue
            parts.append(text[last_index : span.start])
            original = text[span.start : span.end]
            if not original or not original.strip():
                last_index = span.end
                continue
            placeholder = anon_map.add_entity(original, span.entity_type)
            parts.append(placeholder)
            resolved.append(ResolvedSpan(
                start=span.start,
                end=span.end,
                entity_type=span.entity_type,
                placeholder=placeholder,
                score=span.score,
            ))
            last_index = span.end
            count += 1

        parts.append(text[last_index:])
        return "".join(parts), count, resolved

    @staticmethod
    def _log_low_confidence(spans: List[DetectedSpan]) -> None:
        """Log metadata for low-confidence detections without leaking raw PII."""
        for span in spans:
            if span.score < 0.75:
                logger.debug(
                    "Low-confidence PII span detected: type=%s, start=%d, end=%d, score=%.3f",
                    span.entity_type,
                    span.start,
                    span.end,
                    span.score,
                )
