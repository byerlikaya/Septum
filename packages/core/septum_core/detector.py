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
from bisect import bisect_right
from typing import Dict, List, NamedTuple, Optional, Tuple

from presidio_analyzer import AnalyzerEngine, EntityRecognizer, RecognizerResult

from .anonymization_map import SANITIZER_STOPWORDS, AnonymizationMap
from .config import SeptumCoreConfig
from .date_grammar import find_dates
from .date_grammar import warm_up as warm_up_date_grammar
from .language_resolution import (
    canonical_language_code,
    script_min_span_length,
    snaps_whole_run,
)
from .model_integrity import ModelIntegrityError
from .national_ids import IBANValidator
from .ner_model_registry import NERModelRegistry
from .non_pii_filter import NonPiiFilter, SpanView
from .ports import NullSemanticDetectionPort, SemanticDetectionPort
from .recognizers.email import ENTITY_TYPE as EMAIL_ENTITY_TYPE
from .recognizers.email import EmailAddressRecognizer
from .regulations.composer import ComposedPolicy
from .span_processing import (
    absorb_overlapping_spans,
    deduplicate_spans,
    expand_person_name_spans,
    merge_adjacent_person_name_spans,
)
from .spans import DetectedSpan, ResolvedSpan, SanitizeResult, entity_range
from .suffix_list import use_bundled_suffix_list
from .text_boundaries import (
    TOKEN_END,
    TOKEN_START,
    CueTable,
    cue_alternation,
    is_unspaced_char,
    snap_between,
    snap_span,
    space_boundaries,
    token_bounded,
    whole_cue,
    word_count,
    word_cue,
    word_final_cue,
)
from .text_utils import compatibility_fold, normalize_for_comparison, normalize_unicode

use_bundled_suffix_list()

_SPACY_MODEL = "en_core_web_sm"
_cached_nlp_engine: object | None = None
_nlp_engine_lock = threading.Lock()


def _get_shared_nlp_engine() -> object:
    """Return a process-wide Presidio NLP engine singleton.

    Uses ``en_core_web_sm`` instead of the default ``en_core_web_lg`` to
    reduce memory by ~700 MB (no 300-dim word vectors). Presidio's NER
    acts as a supplementary first layer; the dedicated HuggingFace NER
    layer provides the primary person/location detection.

    Raises :class:`~septum_core.model_integrity.ModelIntegrityError` when the
    pinned spaCy model package is missing, instead of letting Presidio
    download an unpinned copy.
    """
    global _cached_nlp_engine
    if _cached_nlp_engine is not None:
        return _cached_nlp_engine
    with _nlp_engine_lock:
        if _cached_nlp_engine is not None:
            return _cached_nlp_engine
        import spacy.util
        from presidio_analyzer.nlp_engine import NlpEngineProvider

        if not spacy.util.is_package(_SPACY_MODEL):
            raise ModelIntegrityError(
                _SPACY_MODEL,
                "spaCy model package is not installed; it is never downloaded at runtime",
            )
        conf = {
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": _SPACY_MODEL}],
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

# Deterministic-recognizer outputs bypass the Ollama validator — only
# probabilistic NER types (PERSON_NAME / LOCATION / ORGANIZATION_NAME)
# need LLM second-guessing, and the validator silently dropping a
# regex-matched address or email is the bug we observed in real KVKK
# forms.
_SEMANTIC_VALIDATION_PASSTHROUGH_TYPES: frozenset[str] = frozenset(
    _HIGH_PRIORITY_ENTITY_TYPES
    | {
        "EMAIL_ADDRESS",
        "URL",
        "IP_ADDRESS",
        "MAC_ADDRESS",
        "DATE_OF_BIRTH",
        "DATE",
        "COORDINATES",
        "COOKIE_ID",
        "DEVICE_ID",
        "POSTAL_ADDRESS",
        "STREET_ADDRESS",
        "CUSTOMER_REFERENCE_ID",
        "EMPLOYEE_ID",
    }
)

# The semantic alias layer resolves later mentions of an entity, which needs
# a few sentences; twelve words is about 80 characters of Latin prose.
_MIN_WORDS_FOR_SEMANTIC_ALIAS = 12

# Per-entity-type minimum confidence for keeping a transformer NER
# detection. Septum is privacy-first: an over-redaction is a recoverable
# annoyance, an under-redaction is a PII leak. The thresholds here
# therefore lean toward recall, especially for high-stakes person
# identifiers, while still gating the entity types that the multilingual
# XLM-RoBERTa variants are known to fire stochastically on short
# locale-specific tokens.
_NER_DEFAULT_MIN_SCORE = 0.85
_NER_MIN_SCORE_PER_TYPE: Dict[str, float] = {
    # PERSON_NAME drives identity redaction; xlm-roberta routinely scores
    # rare Turkish surnames in the 0.80–0.85 band, so the default 0.85
    # was missing real names. Lowered to 0.80 to recapture them — the
    # downstream blocklist + coreference layer can absorb the extra
    # noise this admits.
    "PERSON_NAME": 0.80,
    "EMAIL_ADDRESS": _NER_DEFAULT_MIN_SCORE,
    "ORGANIZATION_NAME": _NER_DEFAULT_MIN_SCORE,
    "LOCATION": _NER_DEFAULT_MIN_SCORE,
}

# Single-word entities of the chosen types are the dominant false-positive
# shape for multilingual NER. They get a stricter floor than multi-word
# spans of the same type. Multi-word spans bypass this gate entirely.
_NER_SINGLE_WORD_MIN_SCORE_PER_TYPE: Dict[str, float] = {
    "ORGANIZATION_NAME": 0.95,
    "LOCATION": 0.95,
}

# When the Presidio layer is run alongside the transformer NER layer,
# Presidio's PERSON_NAME signal is treated as advisory and dropped below
# this floor — the transformer is the authoritative person source.
_PRESIDIO_PERSON_MIN_SCORE_WHEN_NER_ENABLED = 0.7

# Logographic / dense scripts where a 2-glyph span is often a full given
# name. Every other language keeps the default floor, which suppresses
# "Dr" / "Mr" style noise.
_NER_DEFAULT_MIN_SPAN_LEN = 3
_NER_MIN_SPAN_LEN_PER_LANGUAGE: Dict[str, int] = {
    "zh": 2,
    "ja": 2,
    "ko": 2,
    "th": 2,
}

# A single unspaced glyph tagged as a person (e.g. the surname in "老王")
# passes the span-length floor only at this confidence.
# Trade-off: a lone glyph the model scores lower (明 of 小明 at 0.85) is dropped.
_NER_SINGLE_GLYPH_PERSON_MIN_SCORE = 0.95

class _NerGroup(NamedTuple):
    """One raw NER group after label mapping and the score floor."""

    entity_type: str
    start: int
    end: int
    score: float


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

# Detected under every policy, even one whose entity types leave them out:
# every built-in regulation counts an e-mail address as personal data, and a
# policy that omits it would let raw addresses through.
_ALWAYS_DETECTED_ENTITY_TYPES: tuple[str, ...] = (EMAIL_ENTITY_TYPE,)

# Presidio built-ins replaced by a Septum recognizer of the same entity type.
_REPLACED_PRESIDIO_RECOGNIZERS: tuple[str, ...] = ("EmailRecognizer",)

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


# Label cues that put an identifier in context, keyed by ISO 639-1 code; every
# entry is a case-insensitive regex fragment. A plain entry also matches inside
# a compound or before a suffix ("Reisepassnummer", "plakası"). An entry
# wrapped in ``whole_cue`` is a short abbreviation that matches only between
# cue edges ("EIN", "employer_ein", "employerEIN", "EINs", but not the "ein" of
# "protein"); one wrapped in ``word_final_cue`` may end a compound ("Newborn").
# A cue of two letters or fewer is wrapped in ``word_cue`` instead: whole words
# only, so "NOVEMBER" or "HANDLE" is no label.
# FUTURE: move these tables to the DB so each regulation pack can register its
# own preamble vocabulary without code edits.
def _compile_cues(table: CueTable) -> re.Pattern[str]:
    """One case-insensitive alternation of every cue in ``table``."""
    return re.compile(f"(?:{cue_alternation(table)})", re.IGNORECASE)


# National mobile numbers written as one digit run, keyed by ISO 3166-1
# alpha-2 code; every entry is a regex fragment for the national format
# (trunk prefix included). These shapes are distinctive enough to stand alone:
# a trunk "0" or a mobile prefix with an exact length. Countries that share a
# shape each keep their own entry.
_MOBILE_NUMBERS: Dict[str, Tuple[str, ...]] = {
    "AT": (r"06\d{9,10}",),
    "AU": (r"04\d{8}",),
    "BE": (r"04\d{8}",),
    "CH": (r"07[5-9]\d{7}",),
    "CN": (r"1[3-9]\d{9}",),
    "DE": (r"01[5-7]\d{8,9}",),
    "FR": (r"0[67]\d{8}",),
    "GB": (r"07\d{9}",),
    "IE": (r"08[35-9]\d{7}",),
    "JP": (r"0[789]0\d{8}",),
    "KR": (r"01[016-9]\d{7,8}",),
    "NL": (r"06\d{8}",),
    "NZ": (r"02\d{7,9}",),
    "RO": (r"07\d{8}",),
    "RU": (r"89\d{9}",),
    "SA": (r"05\d{8}",),
    "SE": (r"07\d{8}",),
    "TH": (r"0[689]\d{8}",),
    "TR": (r"05\d{9}",),
    "ZA": (r"0[6-8]\d{8}",),
}

# Mobile numbers whose shape many other numbers share (an order number, an
# amount, a timestamp), so they count only next to a phone cue. "US" stands for
# the whole North American Numbering Plan.
_CUED_MOBILE_NUMBERS: Dict[str, Tuple[str, ...]] = {
    "BR": (r"[1-9]{2}9\d{8}",),
    "DK": (r"[2-9]\d{7}",),
    "ES": (r"[67]\d{8}",),
    "GR": (r"69\d{8}",),
    "IN": (r"0?[6-9]\d{9}",),
    "IT": (r"3\d{8,9}",),
    "NO": (r"[49]\d{7}",),
    "PL": (r"[4-8]\d{8}",),
    "PT": (r"9[1236]\d{7}",),
    "SA": (r"5\d{8}",),
    "SG": (r"[89]\d{7}",),
    "TR": (r"5\d{9}",),
    "US": (r"[2-9]\d{2}[2-9]\d{6}",),
}

_PHONE_CUES: CueTable = {
    "en": (
        r"phone",
        r"mobile",
        r"cellular",
        r"contact",
        r"whats\s*app",
        whole_cue("tel|cell|mob|fax|sms|gsm|call|text|pager"),
        word_cue("ph|hp"),
        r"h/p",
    ),
    "tr": (r"telefon", whole_cue("cep", endings=())),
    "de": (r"handy", r"mobil", r"rufnummer"),
    "fr": (r"t[ée]l[ée]phone", whole_cue("tél"), r"portable"),
    "es": (r"tel[ée]fono", r"m[óo]vil", r"celular", whole_cue("tlf|tfno|telf")),
    "pt": (r"telefone", r"telem[óo]vel", r"fone", r"contato", whole_cue("cel|zap")),
    "it": (r"telefono", r"cellulare"),
    "nl": (r"telefoon", r"mobiel"),
    "ru": (r"телефон", whole_cue("тел|моб")),
    "ar": (r"هاتف", r"جوال", r"موبايل"),
    "zh": (r"电话", r"手机", r"手機"),
    "ja": (r"電話", r"携帯"),
    "ko": (r"전화", r"휴대폰", r"핸드폰"),
    "hi": (r"फ़ोन", r"फोन", r"मोबाइल"),
    "th": (r"โทร", r"มือถือ"),
}


# A non-ASCII digit, or a non-ASCII character other than a letter right after
# an ASCII digit (an invisible character inside a run): the only places where
# folding can change what a mobile shape matches.
_FOLDABLE_DIGIT_RE = re.compile(r"(?![0-9])\d|[0-9][^\x00-\x7f\w]")


def _digit_run_pattern(table: Dict[str, Tuple[str, ...]]) -> re.Pattern[str]:
    """One digit run of any shape in ``table``, never part of a longer number or a decimal."""
    shapes = "|".join(shape for entries in table.values() for shape in entries)
    # The leading lookahead fails on most characters before any edge test runs.
    return re.compile(rf"(?=\d)(?<!\d[.,]){token_bounded(shapes)}(?![.,]\d)")


class ExtendedPhoneRecognizer(BaseCustomRecognizer):
    """Presidio recognizer for phone numbers with international format support.

    Phone patterns share a lot of surface area with numeric national
    IDs (Japan My Number, Saudi Iqama, SA ID, Brazilian CPF, …), so a
    loose ``\\d{10,13}`` regex silently eats real identifiers and
    mis-labels them as PHONE_NUMBER. To avoid that, this recognizer
    requires one of the following:

    * an explicit ``+`` international prefix, or
    * at least three digit groups separated by space / dash / dot
      (e.g. ``0505 182 37 96`` or ``(555) 123-4567``), or
    * one digit run with the shape of a national mobile number
      (``_MOBILE_NUMBERS``, e.g. ``13812345678`` or ``05321234567``), or
      with a more common shape next to a phone cue (``_CUED_MOBILE_NUMBERS``,
      e.g. ``Mobile: 9876543210`` or ``9876543210 (mobile)``). Any other bare run such as
      ``701040108923`` is rejected.

    Span length is capped at 9-15 digits to prevent the optional
    international prefix from greedily consuming the first digits of
    an 11-13 digit identifier. A checksum-valid identifier that has a
    mobile shape keeps its own label: ``_ENTITY_TYPE_PRIORITY`` ranks
    it above PHONE_NUMBER for the same span.
    """

    _CONTEXT_WINDOW = 40
    # A cue may also follow the number ("9876543210 (mobile)").
    _TRAILING_WINDOW = 20

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
            rf"\(?{TOKEN_START}\d{{3,4}}\)?[\s\-.]\d{{3,4}}[\s\-.]\d{{2,4}}(?:[\s\-.]\d{{2,4}})?{TOKEN_END}"
            r")"
        )
        self._mobile = _digit_run_pattern(_MOBILE_NUMBERS)
        self._cued_mobile = _digit_run_pattern(_CUED_MOBILE_NUMBERS)
        self._context = _compile_cues(_PHONE_CUES)

    def analyze(  # type: ignore[override]
        self,
        text: str,
        entities: Optional[List[str]] = None,
        nlp_artifacts: Optional[object] = None,
    ) -> List[RecognizerResult]:
        if self._entity_filter(entities):
            return []

        spans: List[Tuple[int, int]] = []
        for match in self._pattern.finditer(text):
            span_text = match.group(0)
            digit_count = sum(1 for c in span_text if c.isdigit())
            if not (9 <= digit_count <= 15):
                continue
            spans.append(match.span())
        # Mobile shapes are matched on the folded text, so fullwidth digits and
        # invisible characters inside the run ("１３８…", "1381234\u200b5678")
        # do not hide it; spans map back to the original offsets. Folding is
        # skipped when no digit could change.
        if _FOLDABLE_DIGIT_RE.search(text):
            folded, origin = compatibility_fold(text)
        else:
            folded, origin = text, range(len(text))
        mobiles = [m.span() for m in self._mobile.finditer(folded)]
        mobiles += [
            m.span()
            for m in self._cued_mobile.finditer(folded)
            if self._context.search(folded, max(0, m.start() - self._CONTEXT_WINDOW), m.start())
            or self._context.search(folded, m.end(), m.end() + self._TRAILING_WINDOW)
        ]
        # Grouped spans come from one scan, so they are sorted and disjoint:
        # the only one that can contain a mobile span starts last before it.
        grouped = list(spans)
        grouped_starts = [s for s, _ in grouped]
        for fold_start, fold_end in sorted(set(mobiles)):
            start, end = origin[fold_start], origin[fold_end - 1] + 1
            i = bisect_right(grouped_starts, start) - 1
            if i < 0 or grouped[i][1] < end:
                spans.append((start, end))
        return [
            RecognizerResult(entity_type="PHONE_NUMBER", start=start, end=end, score=0.8)
            for start, end in spans
        ]


_IBAN_COUNTRY_LENGTHS: dict[str, int] = {
    # ISO 13616 country IBAN lengths (alphanumerics only, no spaces).
    # Used as a safety cap when the greedy regex over-captures past the
    # true IBAN end and no checksum-valid prefix can be found by trimming.
    "AD": 24, "AE": 23, "AL": 28, "AT": 20, "AZ": 28,
    "BA": 20, "BE": 16, "BG": 22, "BH": 22, "BR": 29,
    "BY": 28, "CH": 21, "CR": 22, "CY": 28, "CZ": 24,
    "DE": 22, "DK": 18, "DO": 28, "EE": 20, "EG": 29,
    "ES": 24, "FI": 18, "FO": 18, "FR": 27, "GB": 22,
    "GE": 22, "GI": 23, "GL": 18, "GR": 27, "GT": 28,
    "HR": 21, "HU": 28, "IE": 22, "IL": 23, "IQ": 23,
    "IS": 26, "IT": 27, "JO": 30, "KW": 30, "KZ": 20,
    "LB": 28, "LC": 32, "LI": 21, "LT": 20, "LU": 20,
    "LV": 21, "LY": 25, "MC": 27, "MD": 24, "ME": 22,
    "MK": 19, "MR": 27, "MT": 31, "MU": 30, "NL": 18,
    "NO": 15, "PK": 24, "PL": 28, "PS": 29, "PT": 25,
    "QA": 29, "RO": 24, "RS": 22, "SA": 24, "SC": 31,
    "SE": 24, "SI": 19, "SK": 24, "SM": 27, "ST": 25,
    "SV": 28, "TL": 23, "TN": 24, "TR": 26, "UA": 29,
    "VA": 22, "VG": 24, "XK": 20,
}


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

    Span trimming: the greedy regex can over-capture trailing tokens
    when the IBAN is followed by a label word, country abbreviation,
    or another alphanumeric run separated only by whitespace
    (``...TR94 0006 2000 1010 0007 1234 56 IBAN numarali``). To keep
    the deanonymized output clean the analyzer first tries to find the
    longest checksum-valid prefix by shrinking from the right; failing
    that, the span is capped at the ISO 13616 length for the detected
    country code so a 26-char Turkish IBAN cannot leak the next word
    of context into the placeholder. The over-greedy form would
    otherwise be persisted into ``anon_map.entity_map`` and surface as
    junk text after deanonymization (``[IBAN_1]`` resolved to
    ``TR94 0006 2000 1010 0007 1234 56 IBA``).

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
            rf"{TOKEN_START}[A-Za-z]{{2}}\d{{2}}[\s0-9A-Za-z]{{11,32}}"
        )

    @staticmethod
    def _alnum_count(value: str) -> int:
        """Return the number of alphanumeric characters in ``value``."""
        return sum(1 for ch in value if ch.isalnum())

    @staticmethod
    def _span_for_alnum_count(value: str, target: int) -> int:
        """Return the substring length of ``value`` that contains
        exactly ``target`` alphanumeric characters, or ``len(value)``
        if the string is shorter."""
        seen = 0
        for idx, ch in enumerate(value):
            if ch.isalnum():
                seen += 1
                if seen == target:
                    return idx + 1
        return len(value)

    def _trim_to_valid_iban(self, full: str) -> Optional[str]:
        """Find the longest checksum-valid IBAN prefix of ``full``.

        Walks from the right, dropping one character at a time
        (alphanumeric or separator), and returns the first prefix
        whose checksum validates. ``None`` if no prefix passes.
        """
        candidate = full
        while candidate:
            stripped_len = self._alnum_count(candidate)
            if stripped_len < 15:
                return None
            if stripped_len <= 34 and self._validator.validate(candidate):
                return candidate
            candidate = candidate[:-1]
        return None

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

            valid_prefix = self._trim_to_valid_iban(candidate)
            if valid_prefix is not None:
                # Separators are stripped before the checksum, so a prefix
                # can end in the space after the IBAN: keep it out of the span.
                final = valid_prefix.rstrip()
                score = 0.96
            else:
                # No checksum-valid prefix anywhere in the matched span.
                # Cap at the ISO 13616 length for the country code so a
                # mis-typed / synthetic IBAN cannot drag along the next
                # word of context. Fall back to the bare 15-char minimum
                # if the country code is unrecognized.
                country = candidate[:2].upper()
                max_alnum = _IBAN_COUNTRY_LENGTHS.get(country)
                if max_alnum is not None:
                    final = candidate[: self._span_for_alnum_count(candidate, max_alnum)].rstrip()
                else:
                    final = candidate
                stripped_len = self._alnum_count(final)
                if not (15 <= stripped_len <= 34):
                    continue
                score = 0.65

            start = match.start()
            end = start + len(final)
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
            r":[^\S\n]*+\n\s*+([^\W\d_]{2,20})[^\S\n]*+(?:\n|$)",
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
            rf"(?:(?:{abbr_pattern}){TOKEN_END}\.?)"
            r"|"
            r"(?:(?<![A-Za-z])[A-Za-z]++\s*+[:./]\s*+\d+)",
            re.IGNORECASE | re.UNICODE,
        )
        self._numeric_density = re.compile(
            r"\d+",
        )
        self._label_value = re.compile(
            r"^(\S[^:\n]{1,40}+)\s*+:\s*+(.+)$",
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

        results = self._labeled_field_results(text)
        results.extend(self._dense_line_results(text, results))
        return results

    def _labeled_field_results(self, text: str) -> List[RecognizerResult]:
        """Strategy 1: ``Label : value`` fields whose value looks like an address.

        The value must hold multiple structural cues, or a cue and several
        numbers, and be at least 10 characters long.
        """
        results: List[RecognizerResult] = []
        for label_match in self._label_value.finditer(text):
            value = label_match.group(2).strip()
            cue_count = len(self._structural_cue.findall(value))
            num_count = len(self._numeric_density.findall(value))
            looks_like_address = cue_count >= 2 or (cue_count >= 1 and num_count >= 2)
            if looks_like_address and len(value) >= 10:
                results.append(
                    RecognizerResult(
                        entity_type="POSTAL_ADDRESS",
                        start=label_match.start(2),
                        end=label_match.end(2),
                        score=0.82,
                    )
                )
        return results

    def _dense_line_results(
        self, text: str, covering: List[RecognizerResult]
    ) -> List[RecognizerResult]:
        """Strategy 2: lines with 2+ cues that also contain numeric tokens.

        Building numbers and postal codes count as numeric tokens. A line a
        ``covering`` result already spans is skipped.
        """
        results: List[RecognizerResult] = []
        for line_match in re.finditer(r"[^\n]+", text):
            line = line_match.group()
            structural_hits = len(self._structural_cue.findall(line))
            numeric_hits = len(self._numeric_density.findall(line))
            if structural_hits < 2 or numeric_hits < 1:
                continue
            line_start = line_match.start()
            line_end = line_match.end()
            if any(r.start <= line_start and r.end >= line_end for r in covering + results):
                continue
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
            token_bounded(
                r"(?:4\d{3}|5[1-5]\d{2}|2[2-7]\d{2}|3[4-9]\d{2}|3[0-3]\d{2}"
                r"|6(?:011|5\d{2}|4[4-9]\d|22[1-9]|\d{3})"
                r"|35\d{2})"
                r"[\s\-]?\d{3,4}[\s\-]?\d{3,4}[\s\-]?\d{3,5}"
            )
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


_BIRTH_DATE_CUES: CueTable = {
    "en": (
        whole_cue(r"d\.?o\.?b"),
        r"birth\s*date",
        r"date\s*of\s*birth",
        word_final_cue("born"),
    ),
    "fr": (r"naissance",),
    "es": (r"nacimiento", r"fecha\s*de\s*nacimiento"),
    "pt": (r"nascimento", r"data\s*de\s*nascimento"),
    "de": (r"geburtsdatum",),
    "nl": (r"geboortedatum",),
    "it": (r"data\s*di\s*nascita",),
    "tr": (r"do[gğ]um\s*tarihi",),
    "ru": (r"дата\s*рождения",),
    "ar": (r"تاريخ\s*الميلاد",),
    "zh": (r"出生日期",),
    "ja": (r"生年月日",),
    "hi": (r"जन्म\s*तिथि",),
}


class CalendarDateRecognizer(BaseCustomRecognizer):
    """Detects calendar dates as ``DATE`` and birth dates as ``DATE_OF_BIRTH``.

    One scan of the shared date grammar (:mod:`septum_core.date_grammar`)
    serves both types. A date with a birth-context keyword within 40
    characters before it is emitted as ``DATE_OF_BIRTH`` when that type is
    requested; every other date is emitted as ``DATE`` (score 0.85, no
    context needed) when ``DATE`` is requested. Building the recognizer
    compiles the grammar, so the first request does not pay for it.
    """

    _DOB_SCORE = 0.96
    _DATE_SCORE = 0.85
    _CONTEXT_WINDOW = 40

    def __init__(self, supported_entities: Optional[List[str]] = None) -> None:
        super().__init__(
            supported_entities=supported_entities or ["DATE", "DATE_OF_BIRTH"],
            supported_language="en",
        )
        warm_up_date_grammar()
        self._context_pattern = _compile_cues(_BIRTH_DATE_CUES)

    def analyze(self, text: str, entities: Optional[List[str]] = None,
                nlp_artifacts: Optional[object] = None) -> List[RecognizerResult]:
        if self._entity_filter(entities):
            return []
        wanted = set(self.supported_entities)
        if entities:
            wanted &= set(entities)
        results: List[RecognizerResult] = []
        for date in find_dates(text):
            window = text[max(0, date.start - self._CONTEXT_WINDOW):date.start]
            if "DATE_OF_BIRTH" in wanted and self._context_pattern.search(window):
                entity_type, score = "DATE_OF_BIRTH", self._DOB_SCORE
            elif "DATE" in wanted:
                entity_type, score = "DATE", self._DATE_SCORE
            else:
                continue
            results.append(RecognizerResult(
                entity_type=entity_type, start=date.start, end=date.end, score=score,
            ))
        return results


class DateOfBirthRecognizer(CalendarDateRecognizer):
    """``CalendarDateRecognizer`` restricted to ``DATE_OF_BIRTH`` output."""

    def __init__(self) -> None:
        super().__init__(supported_entities=["DATE_OF_BIRTH"])


class MACAddressRecognizer(BaseCustomRecognizer):
    """Detects MAC addresses in colon, dash, or dot notation."""

    def __init__(self) -> None:
        super().__init__(
            supported_entities=["MAC_ADDRESS"],
            supported_language="en",
        )
        self._pattern = re.compile(
            token_bounded(
                r"(?:[0-9A-Fa-f]{2}[:\-]){5}[0-9A-Fa-f]{2}"
                r"|(?:[0-9A-Fa-f]{4}\.){2}[0-9A-Fa-f]{4}"
            )
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
            r"-?\d{1,3}\.\d{3,8}(?:\s*+°)?+(?:\s*+[NSns])?+(?>\s*+[,/;]\s*+|\s++)"
            r"-?\d{1,3}\.\d{3,8}\s*+°?\s*+[EWew]?"
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
            r"(?:serial(?:\s*+(?:number|no|#))?|s/n)\s*+[=:]\s*+[A-Za-z0-9]{8,20}",
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


_SOCIAL_SECURITY_CUES: CueTable = {
    "en": (whole_cue("SSN"), r"social\s*security"),
}


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
        self._pattern = re.compile(token_bounded(r"\d{3}[\s\-]?\d{2}[\s\-]?\d{4}"))
        self._context = _compile_cues(_SOCIAL_SECURITY_CUES)

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


_INDIVIDUAL_TAXPAYER_CUES: CueTable = {
    "pt": (whole_cue("cpf"),),
}


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
        self._pattern = re.compile(token_bounded(r"\d{3}\.?\d{3}\.?\d{3}[\-.]?\d{2}"))
        self._context = _compile_cues(_INDIVIDUAL_TAXPAYER_CUES)

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


_PASSPORT_CUES: CueTable = {
    "en": (r"passport",),
    "tr": (r"pasaport",),
    "de": (r"reisepass",),
    "fr": (r"passeport",),
    "it": (r"passaporto",),
    "nl": (r"paspoort",),
    "ru": (r"паспорт",),
    "ar": (r"جواز\s*سفر",),
    "zh": (r"护照",),
    "ja": (r"パスポート",),
    "hi": (r"पासपोर्ट",),
}


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
        self._pattern = re.compile(token_bounded(r"[A-Z]{0,2}\d{6,9}"))
        self._context = _compile_cues(_PASSPORT_CUES)

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


_DRIVING_LICENCE_CUES: CueTable = {
    "en": (r"driver'?s?\s*licen[sc]e", r"driving\s*licen[sc]e", word_cue("DL"), whole_cue("CDL|DLN|OLN")),
    "tr": (r"ehliyet",),
    "fr": (r"permis\s*de\s*conduire",),
    "de": (r"führerschein",),
    "nl": (r"rijbewijs",),
    "it": (r"patente\s*di\s*guida",),
    "es": (r"licencia\s*de\s*conducir",),
    "pt": (r"carta\s*de\s*condução",),
    "ru": (r"водительское\s*удостоверение",),
}


class DriversLicenseRecognizer(BaseCustomRecognizer):
    """Detects driver's license numbers with context keywords.

    Formats vary widely; requires contextual keyword to reduce false positives.
    """

    def __init__(self) -> None:
        super().__init__(
            supported_entities=["DRIVERS_LICENSE"],
            supported_language="en",
        )
        self._pattern = re.compile(token_bounded(r"[A-Z0-9]{5,15}"))
        self._context = _compile_cues(_DRIVING_LICENCE_CUES)

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


_TAX_ID_CUES: CueTable = {
    "en": (r"tax\s*id", whole_cue("TIN|EIN|FEIN|ITIN")),
    "tr": (r"vergi\s*(?:no|numarası|kimlik)",),
    "de": (
        r"steuer[\-\s]?(?:nummer|id)",
        r"USt[\-\s]?Identifikationsnummer",
        whole_cue(r"ust[\-\s]?id"),
        whole_cue(r"USt[\-\s]?IdNr"),
    ),
    "es": (whole_cue("NIF|CIF|NIE"),),
    "fr": (
        r"N(?:º|[oO]\.?)\s*SIREN",
        whole_cue("SIRET"),
        r"tva\s*intracommunautaire",
        r"numéro\s*fiscal",
    ),
    "it": (r"codice\s*fiscale", r"partita\s*iva"),
    "pt": (r"inscrição\s*estadual", whole_cue("CNPJ|CPF")),
    "ru": (whole_cue("ИНН", endings=("а", "у", "е", "ом", "ы", "ов")),),
    "ar": (r"الرقم\s*الضريبي",),
    "zh": (r"税号",),
}


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
            token_bounded(
                r"[A-Z]{2}\s?\d{2}\s?\d{6,10}"
                r"|[A-Z]-?\d{7,8}[A-Z0-9]?"
                r"|\d{2,3}/\d{3,4}/\d{4,5}"
                r"|\d{2,3}(?:\.\d{3}){2,3}(?:[-.]\d{1,2})?"
                r"|\d{2}\s\d{3}\s\d{3}\s\d{3}"
                r"|\d{2}[\-]?\d{7,10}"
            ),
            re.IGNORECASE,
        )
        self._context = _compile_cues(_TAX_ID_CUES)

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


_LICENSE_PLATE_CUES: CueTable = {
    "en": (r"licen[sc]e\s*plate", r"plate\s*(?:no|number)", r"vehicle\s*reg"),
    "tr": (r"plaka",),
    "de": (r"kennzeichen",),
    "fr": (r"immatriculation",),
    "it": (r"targa",),
    "es": (r"matrícula",),
    "nl": (r"kenteken",),
    "ru": (r"номерной\s*знак",),
    "ar": (r"لوحة\s*السيارة",),
    "zh": (r"车牌",),
    "ja": (r"ナンバープレート",),
}


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
            token_bounded(
                r"\d{2}\s*[A-Z]{1,3}\s*\d{2,4}"
                r"|[A-Z]{1,3}[\s\-]?\d{1,4}(?:[\s\-]?[A-Z]{1,3})?"
            )
        )
        self._context = _compile_cues(_LICENSE_PLATE_CUES)

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
        except Exception as exc:
            # Surface the error so a Presidio API change does not
            # silently disable coverage warnings — privacy-first
            # over-detection is preferable to a silent miss.
            logger.warning(
                "Skipping entity coverage validation: %s", type(exc).__name__
            )
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

        1. the original types, plus ``_ALWAYS_DETECTED_ENTITY_TYPES``,
        2. each type's Presidio built-in alias (``CREDIT_CARD_NUMBER``
           → ``CREDIT_CARD``, ``IBAN`` → ``IBAN_CODE``),
        3. each type's ``_PARENT_TYPE_COVERAGE`` parent (e.g.
           ``BANK_ACCOUNT_NUMBER`` → ``IBAN``).

        Without the parent-coverage expansion, Presidio's entity filter
        would hide the IBAN / PERSON_NAME detectors whenever a
        regulation only names the child alias, which is a silent
        detection regression. An empty or missing list stays as it is:
        Presidio then runs every recognizer.
        """
        if not entity_types:
            return entity_types
        expanded = list(entity_types)
        for always in _ALWAYS_DETECTED_ENTITY_TYPES:
            if always not in expanded:
                expanded.append(always)
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
        for name in _REPLACED_PRESIDIO_RECOGNIZERS:
            registry.remove_recognizer(name)
        registry.add_recognizer(EmailAddressRecognizer())
        registry.add_recognizer(ExtendedPhoneRecognizer())
        registry.add_recognizer(ValidatedIBANRecognizer())
        registry.add_recognizer(HeuristicPersonNameRecognizer())
        registry.add_recognizer(StructuralAddressRecognizer())
        registry.add_recognizer(CreditCardNumberRecognizer())
        registry.add_recognizer(CalendarDateRecognizer())
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
            spans.extend(self._presidio_spans(normalized_text))

        # NER runs on text of any length: a short chat question or chunk can
        # hold a name, and any length floor would leave it raw.
        if self._config.use_ner_layer and normalized_text.strip():
            spans.extend(self._ner_spans(normalized_text, language))

        port_enabled = self._semantic_port.is_enabled()
        spans = self._validate_semantically(
            spans, normalized_text, language, port_enabled
        )
        if self._runs_semantic_detection(normalized_text, port_enabled):
            spans.extend(self._semantic_detection_spans(normalized_text))

        spans = self._apply_non_pii_filter(spans, normalized_text, language)
        spans = self._resolve_overlaps(spans, normalized_text, language)

        sanitized, count, resolved_spans = self._apply_replacements(
            normalized_text, spans, anon_map, language
        )

        if self._config.use_semantic_detection and port_enabled and anon_map.entity_map:
            self._resolve_coreference(normalized_text, anon_map, language)

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

    def _presidio_spans(self, normalized_text: str) -> List[DetectedSpan]:
        """Run the Presidio layer and return its filtered results as spans."""
        results = self._analyzer.analyze(
            text=normalized_text,
            language=_PRESIDIO_DEFAULT_LANGUAGE,
            entities=self._expand_with_aliases(self._entity_types),
        )
        results = self._filter_presidio_results(results, normalized_text)
        return self._from_presidio_results(results)

    def _ner_spans(self, normalized_text: str, language: str) -> List[DetectedSpan]:
        """Run the NER ensemble and return its unique spans for the active types.

        Pass 1: every ensemble member sees the original text. Different
        architectures catch different rare names; identical
        ``(start, end, entity_type)`` spans are counted once.

        Pass 2: ALL-CAPS recovery. Title-casing fixes the shared uppercase
        blind spot of mixed-case-trained transformers, but rare-surname recall
        is still architecture-dependent — that is why the registry pairs
        models in the first place. Every ensemble member also runs on the
        title-cased text so the backstop model (e.g. savasy BERT for Turkish)
        still sees inputs whose only appearance is ALL CAPS.
        """
        ner_pipelines = self._ner_registry.get_pipelines(language)
        titlecased = _titlecase_upper_segments(normalized_text)
        variants = [normalized_text]
        if titlecased != normalized_text:
            variants.append(titlecased)
        unique: Dict[tuple[int, int, str], DetectedSpan] = {}
        for variant in variants:
            for ner_pipeline in ner_pipelines:
                for span in self._from_ner_results(
                    ner_pipeline(variant), normalized_text, language
                ):
                    unique.setdefault((span.start, span.end, span.entity_type), span)
        ner_spans = list(unique.values())
        if self._entity_types is None:
            return ner_spans
        allowed = set(self._expand_with_aliases(self._entity_types))
        return [s for s in ner_spans if s.entity_type in allowed]

    def _validate_semantically(
        self,
        spans: List[DetectedSpan],
        normalized_text: str,
        language: str,
        port_enabled: bool,
    ) -> List[DetectedSpan]:
        """Run the semantic validation layer; on failure keep every candidate."""
        if not (self._config.use_semantic_validation and port_enabled and spans):
            return spans
        try:
            validated_spans = self._semantic_validate_candidates(
                text=normalized_text,
                candidate_spans=spans,
                language=language,
            )
        except Exception as e:
            logger.warning(
                "Semantic validation layer failed, continuing with all candidates: %s",
                type(e).__name__,
            )
            return spans
        logger.debug(
            "Semantic validation: %d candidates → %d validated spans",
            len(spans),
            len(validated_spans),
        )
        return validated_spans

    def _runs_semantic_detection(self, normalized_text: str, port_enabled: bool) -> bool:
        """Whether the alias and contextual layers run for this text."""
        return (
            self._config.use_semantic_detection
            and port_enabled
            and word_count(normalized_text.strip()) >= _MIN_WORDS_FOR_SEMANTIC_ALIAS
        )

    def _semantic_detection_spans(self, normalized_text: str) -> List[DetectedSpan]:
        """Collect the alias and contextual layer spans; a failing layer adds none."""
        spans: List[DetectedSpan] = []
        try:
            semantic_spans = self._semantic_port.detect_aliases(
                normalized_text=normalized_text,
            )
            logger.debug(
                "Semantic alias layer returned %d spans",
                len(semantic_spans),
            )
            spans.extend(semantic_spans)
        except Exception as e:
            logger.warning(
                "Semantic alias layer failed, continuing without it: %s", type(e).__name__
            )

        if self._config.use_semantic_contextual_detection:
            spans.extend(self._contextual_spans(normalized_text))
        return spans

    def _contextual_spans(self, normalized_text: str) -> List[DetectedSpan]:
        """Run the contextual layer for the active contextual entity types."""
        semantic_types = [
            et for et in (self._entity_types or [])
            if et in _CONTEXTUAL_ENTITY_TYPES
        ]
        if not semantic_types:
            return []
        try:
            contextual_spans = self._semantic_port.detect_semantic(
                normalized_text=normalized_text,
                entity_types=semantic_types,
            )
        except Exception as e:
            logger.warning("Semantic contextual layer failed: %s", type(e).__name__)
            return []
        logger.debug(
            "Semantic contextual layer returned %d spans",
            len(contextual_spans),
        )
        return list(contextual_spans)

    def _apply_non_pii_filter(
        self, spans: List[DetectedSpan], normalized_text: str, language: str
    ) -> List[DetectedSpan]:
        """Drop the spans the non-PII filter rejects; keep the originals of the rest."""
        if self._non_pii_filter is None:
            return spans
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
            original = next(
                (
                    s for s in spans
                    if s.start == view.start
                    and s.end == view.end
                    and s.entity_type == view.entity_type
                    and s.score == view.score
                ),
                None,
            )
            if original is not None:
                keep_spans.append(original)
        return keep_spans

    @staticmethod
    def _resolve_overlaps(
        spans: List[DetectedSpan], normalized_text: str, language: str
    ) -> List[DetectedSpan]:
        """Absorb, deduplicate, grow and merge spans, then drop stopword spans.

        Person-name spans are expanded to cover adjacent capitalized tokens so
        that given-name-only detections are upgraded to full name blocks where
        possible. This is language-agnostic and relies only on character
        casing and token boundaries.
        """
        spans = absorb_overlapping_spans(spans, text=normalized_text)
        spans = deduplicate_spans(
            spans, _HIGH_PRIORITY_ENTITY_TYPES, text=normalized_text
        )
        spans = expand_person_name_spans(normalized_text, spans)
        spans = merge_adjacent_person_name_spans(normalized_text, spans)
        return [
            s
            for s in spans
            if normalize_for_comparison(
                normalized_text[s.start : s.end].strip(), language
            )
            not in SANITIZER_STOPWORDS
        ]

    def _resolve_coreference(
        self, normalized_text: str, anon_map: AnonymizationMap, language: str
    ) -> None:
        """Run pronoun coreference resolution; a failure leaves the map as it is."""
        try:
            self._semantic_port.resolve_coreference(
                normalized_text=normalized_text,
                anon_map=anon_map,
                language=language,
            )
        except Exception as e:
            logger.warning(
                "Pronoun coreference resolution failed, continuing: %s", type(e).__name__
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
        return [r for r in results if not self._drops_presidio_result(r, text)]

    def _drops_presidio_result(self, r: RecognizerResult, text: str) -> bool:
        """Whether a Presidio result is advisory noise the other layers cover better."""
        if (
            self._config.use_ner_layer
            and r.entity_type == "PERSON_NAME"
            and r.score < _PRESIDIO_PERSON_MIN_SCORE_WHEN_NER_ENABLED
        ):
            return True

        if r.entity_type == "PHONE_NUMBER":
            span_text = text[r.start : r.end]
            digit_count = sum(1 for ch in span_text if ch.isdigit())
            has_dot_or_slash = "." in span_text or "/" in span_text
            if has_dot_or_slash and digit_count <= 8:
                return True

        # Drop Presidio's spaCy-LOCATION output ONLY when the NER layer is
        # also active — the transformer ensemble supplies higher-quality
        # LOCATION spans anyway. With the NER layer disabled (e.g.
        # transformers extra not installed) Presidio's LOCATION is the only
        # signal, so let it through even though en_core_web_sm is noisy on
        # non-English text.
        return r.entity_type == "LOCATION" and self._config.use_ner_layer

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
    def _merge_touching_ner_groups(
        raw_results: List[dict],
    ) -> List[_NerGroup]:
        """Map and score-filter raw NER groups, then join same-type groups that touch.

        A model can tag the glyphs of one name as separate groups (B-PER,
        B-PER for a two-glyph name); joined, the name clears the span-length
        floor that each glyph alone would fail. The joined group keeps the
        higher score. Empty groups (``end <= start``) are dropped.
        """
        groups = [g for g in map(Detector._ner_group, raw_results) if g is not None]

        merged: List[_NerGroup] = []
        for group in sorted(groups, key=lambda g: (g.start, g.end)):
            last = merged[-1] if merged else None
            if last is not None and last.entity_type == group.entity_type and last.end == group.start:
                merged[-1] = last._replace(end=group.end, score=max(last.score, group.score))
            else:
                merged.append(group)
        return merged

    @staticmethod
    def _ner_group(item: dict) -> Optional[_NerGroup]:
        """Map one raw NER item to a group, or None when it is empty, unmapped or below the score floor."""
        start, end = int(item["start"]), int(item["end"])
        if end <= start:
            return None
        entity = item.get("entity_group") or item.get("entity")
        if not entity:
            return None
        entity_type = Detector._map_ner_label(entity)
        if entity_type is None:
            return None
        score = float(item.get("score", 0.0))
        if score < _NER_MIN_SCORE_PER_TYPE.get(entity_type, _NER_DEFAULT_MIN_SCORE):
            return None
        return _NerGroup(entity_type, start, end, score)

    @staticmethod
    def _snap(
        text: str, start: int, end: int, whole_run: bool, boundaries: List[int]
    ) -> Tuple[int, int]:
        """Snap a span to the whole unspaced run (``boundaries`` from ``space_boundaries``) or to its word edges."""
        if whole_run:
            return snap_between(boundaries, len(text), start, end)
        return snap_span(text, start, end)

    @staticmethod
    def _from_ner_results(
        raw_results: List[dict],
        text: str,
        language: str = "en",
    ) -> List[DetectedSpan]:
        """Convert HuggingFace NER pipeline outputs to DetectedSpan.

        Confidence thresholds come from ``_NER_MIN_SCORE_PER_TYPE`` (and
        ``_NER_SINGLE_WORD_MIN_SCORE_PER_TYPE`` for ORG/LOC single-word
        spans) so per-type tuning happens in one place. Spans are snapped
        to word boundaries to prevent mid-word replacements caused by
        subword tokenisation.

        Minimum span length follows each span's own script first: a span
        written in a script whose rule sets a floor (Han, Hangul, kana,
        Thai — names routinely run 2–3 glyphs, e.g. "李明", "王芳") keeps
        that floor in a document of any language. Other spans use the
        document language's floor, 3 characters by default (Latin scripts
        produce too many 2-letter false positives otherwise). Touching
        same-type groups are joined first; a lone unspaced glyph passes as a
        person only at ``_NER_SINGLE_GLYPH_PERSON_MIN_SCORE``.

        A group in a script flagged ``snap_whole_run`` in the script table
        snaps to the whole unspaced run and counts words by whitespace. The
        choice follows the script of the group, not the document language.

        LOCATION entities pass through a conservative multi-word-or-high-
        score gate (same shape as ORGANIZATION_NAME) to drop the locale-
        specific single-token mis-fires that motivated the earlier full
        suppression. See ``_map_ner_label`` for the rationale.
        """
        language_floor = _NER_MIN_SPAN_LEN_PER_LANGUAGE.get(
            canonical_language_code(language), _NER_DEFAULT_MIN_SPAN_LEN
        )
        groups = Detector._merge_touching_ner_groups(raw_results)
        ascii_text = text.isascii()
        whole_runs = [
            not ascii_text and snaps_whole_run(text[start:end])
            for _, start, end, _ in groups
        ]
        boundaries = space_boundaries(text) if any(whole_runs) else []
        spans: List[DetectedSpan] = []
        for (entity_type, start, end, score), whole_run in zip(groups, whole_runs):
            start, end = Detector._snap(text, start, end, whole_run, boundaries)

            span_text = text[start:end].strip()
            if not Detector._clears_ner_floors(
                entity_type, span_text, score, whole_run, language_floor
            ):
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
    def _clears_ner_floors(
        entity_type: str,
        span_text: str,
        score: float,
        whole_run: bool,
        language_floor: int,
    ) -> bool:
        """Whether a snapped NER span meets the length floor and the single-word score floor."""
        script_floor = script_min_span_length(span_text)
        min_span_len = script_floor if script_floor is not None else language_floor
        single_glyph_person = (
            entity_type == "PERSON_NAME"
            and len(span_text) == 1
            and is_unspaced_char(span_text)
            and score >= _NER_SINGLE_GLYPH_PERSON_MIN_SCORE
        )
        if len(span_text) < min_span_len and not single_glyph_person:
            return False

        single_word_floor = _NER_SINGLE_WORD_MIN_SCORE_PER_TYPE.get(entity_type)
        if single_word_floor is None:
            return True
        # Locale-specific single-token mis-fires (Turkish "Doğum", German form
        # headers, …) are the dominant FP shape for multilingual XLM-RoBERTa
        # on ORG/LOC. Multi-word spans bypass; single tokens must clear a
        # higher floor.
        words = len(span_text.split()) if whole_run else word_count(span_text)
        return words >= 2 or score >= single_word_floor

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
        # Use exact set membership instead of substring ``in`` checks:
        # "PER" in "OPERATION" or "SUPER" used to match too, mis-tagging
        # third-party NER outputs as person names. The set covers every
        # tag scheme we have seen in the wild (CoNLL B-/I- prefixes,
        # OntoNotes long-form, plain "PER" / "ORG" / "LOC").
        person_tags = {"PER", "PERSON", "B-PER", "I-PER", "B-PERSON", "I-PERSON"}
        org_tags = {"ORG", "ORGANIZATION", "B-ORG", "I-ORG", "B-ORGANIZATION", "I-ORGANIZATION"}
        location_tags = {
            "LOC", "LOCATION", "GPE",
            "B-LOC", "I-LOC", "B-LOCATION", "I-LOCATION",
            "B-GPE", "I-GPE",
        }
        upper = label.upper()
        if upper in person_tags:
            return "PERSON_NAME"
        if upper in {"EMAIL", "B-EMAIL", "I-EMAIL", "EMAIL_ADDRESS"}:
            return "EMAIL_ADDRESS"
        if upper in org_tags:
            return "ORGANIZATION_NAME"
        if upper in location_tags:
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
            core_start, core_end = entity_range(span.start, span.end, span.core, text)
            placeholder = anon_map.add_entity(
                text[core_start:core_end], span.entity_type
            )
            parts.append(placeholder)
            resolved.append(ResolvedSpan(
                start=span.start,
                end=span.end,
                entity_type=span.entity_type,
                placeholder=placeholder,
                score=span.score,
                core=span.core,
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
