from __future__ import annotations

"""
Backward-compatibility shim over :mod:`septum_core.detector`.

The backend-side :class:`PIISanitizer` wraps the core
:class:`septum_core.detector.Detector` and injects an Ollama-backed
:class:`SemanticDetectionPort` adapter that restores the Layer 3 /
Layer 4 behaviour of the original monolithic sanitizer:

* ``_ollama_validate_pii_candidates`` → :meth:`OllamaSemanticAdapter.validate_candidates`
* ``_ollama_pii_detection``            → :meth:`OllamaSemanticAdapter.detect_aliases`
* ``_ollama_semantic_detection``       → :meth:`OllamaSemanticAdapter.detect_semantic`
* ``_resolve_pronoun_coreference``     → :meth:`OllamaSemanticAdapter.resolve_coreference`

The adapter receives a live :class:`AppSettings` instance so the
chat-model URL and model ID can still be configured from the database
without leaking any of that wiring into the air-gapped core package.
"""

import logging
from typing import List, Optional

from septum_core.anonymization_map import AnonymizationMap
from septum_core.config import SeptumCoreConfig
from septum_core.detector import (
    _CONTEXTUAL_ENTITY_TYPES,
    _HIGH_PRIORITY_ENTITY_TYPES,
    _NER_LAYER_ENTITY_TYPES,
    _PARENT_TYPE_COVERAGE,
    _PRESIDIO_DEFAULT_LANGUAGE,
    _PRESIDIO_ENTITY_ALIASES,
    _PRESIDIO_REVERSE_ALIASES,
    BaseCustomRecognizer,
    CookieIDRecognizer,
    CoordinatesRecognizer,
    CPFRecognizer,
    CreditCardNumberRecognizer,
    DateOfBirthRecognizer,
    Detector,
    DeviceIDRecognizer,
    DriversLicenseRecognizer,
    ExtendedPhoneRecognizer,
    HeuristicPersonNameRecognizer,
    LicensePlateRecognizer,
    MACAddressRecognizer,
    PassportNumberRecognizer,
    SSNRecognizer,
    StructuralAddressRecognizer,
    TaxIDRecognizer,
    URLRecognizer,
    ValidatedIBANRecognizer,
)
from septum_core.detector import (
    _SEMANTIC_VALIDATION_PASSTHROUGH_TYPES as _OLLAMA_VALIDATION_PASSTHROUGH_TYPES,
)
from septum_core.ner_model_registry import NERModelRegistry
from septum_core.ports import SemanticDetectionPort
from septum_core.regulations.composer import ComposedPolicy
from septum_core.spans import DetectedSpan, ResolvedSpan, SanitizeResult
from septum_core.text_boundaries import is_span_boundary
from septum_core.text_utils import lower_preserving_offsets

from ..errors import SemanticLayerUnavailableError
from ..models.settings import AppSettings
from .contextual_placement import (
    _CONTEXTUAL_SLICE_OVERLAP,
    _MIN_SLICE_BYTES,
    _contextual_slices,
    _reply_spans,
)
from .ollama_client import (
    OLLAMA_NUM_PREDICT,
    OllamaCallError,
    call_ollama_sync,
    extract_json_array,
    ollama_context_window,
    reply_items,
    request_ollama_sync,
)
from .prompts import PromptCatalog

logger = logging.getLogger(__name__)

_ALIAS_ENTITY_TYPES: frozenset[str] = frozenset({"PERSON_NAME", "ALIAS", "FIRST_NAME", "LAST_NAME"})
_PERSON_PLACEHOLDER_TYPES: tuple[str, ...] = ("PERSON_NAME", "FIRST_NAME", "LAST_NAME", "ALIAS")
_NUMERIC_SYMBOLS: tuple[str, ...] = ("$", "€", "£", "¥", "%")
_NUMERIC_HEAVY_RATIO = 0.4


def _candidate_payload(text: str, candidate_spans: List[DetectedSpan]) -> list[dict]:
    """Serialize candidate spans into the shape the validation prompt expects."""
    return [
        {
            "text": text[span.start : span.end],
            "entity_type": span.entity_type,
            "start": span.start,
            "end": span.end,
            "score": span.score,
        }
        for span in candidate_spans
    ]


def _matching_candidate(item: object, candidate_spans: List[DetectedSpan]) -> Optional[DetectedSpan]:
    """Return the candidate whose offsets and type match a validated item, if any."""
    if not isinstance(item, dict):
        return None
    start = item.get("start")
    end = item.get("end")
    entity_type = item.get("entity_type")
    if item.get("text") is None or entity_type is None or start is None or end is None:
        return None
    for original_span in candidate_spans:
        if (
            original_span.start == start
            and original_span.end == end
            and original_span.entity_type == entity_type
        ):
            return original_span
    return None


def _is_numeric_like_line(line: str) -> bool:
    """Return whether at least half of a line's tokens carry digits or currency/percent signs."""
    tokens = line.split()
    if not tokens:
        return False
    digit_tokens = 0
    for token in tokens:
        if any(ch.isdigit() for ch in token):
            digit_tokens += 1
        if any(ch in token for ch in _NUMERIC_SYMBOLS):
            digit_tokens += 1
    return digit_tokens >= max(1, len(tokens) // 2)


def _is_numeric_heavy(normalized_text: str) -> bool:
    """Return whether the text is numeric-heavy structured content, logging the skip."""
    lines = [ln.strip() for ln in normalized_text.splitlines() if ln.strip()]
    if not lines:
        return False
    numeric_like = sum(1 for line in lines if _is_numeric_like_line(line))
    if numeric_like / max(1, len(lines)) < _NUMERIC_HEAVY_RATIO:
        return False
    logger.debug(
        "Skipping Ollama alias layer for numeric-heavy structured text "
        "(%d/%d lines classified as numeric-like).",
        numeric_like,
        len(lines),
    )
    return True


def _unique_span_offsets(normalized_text: str, text_str: str) -> Optional[tuple[int, int]]:
    """Locate a single, word-bounded occurrence of ``text_str`` (case-insensitive).

    Both sides are lowercased without changing their length, so an offset
    in the lowered text is the same offset in ``normalized_text`` even
    after a dotted capital ``İ``. Returns ``None`` when the value is
    absent, not on a word boundary, or occurs more than once.
    """
    text_lower = lower_preserving_offsets(normalized_text)
    search_lower = lower_preserving_offsets(text_str)
    idx = text_lower.find(search_lower)
    if idx < 0:
        return None
    end_idx = idx + len(text_str)
    if not is_span_boundary(normalized_text, idx, end_idx):
        return None
    if text_lower.count(search_lower) > 1:
        return None
    return idx, end_idx


def _alias_span(item: object, normalized_text: str) -> Optional[DetectedSpan]:
    """Turn one alias-layer item into a span, or ``None`` when it is rejected."""
    if not isinstance(item, dict):
        return None
    text_val = item.get("text")
    entity_type = item.get("entity_type") or item.get("type") or "ALIAS"
    if text_val is None or text_val == "":
        return None
    entity_type_upper = str(entity_type).upper().replace(" ", "_")
    if entity_type_upper not in _ALIAS_ENTITY_TYPES:
        return None
    text_str = str(text_val).strip()
    if len(text_str) < 3:
        return None
    offsets = _unique_span_offsets(normalized_text, text_str)
    if offsets is None:
        return None
    return DetectedSpan(
        start=offsets[0],
        end=offsets[1],
        entity_type=entity_type_upper,
        score=0.85,
    )


_CHAT_TEMPLATE_MARGIN_TOKENS = 256


def _contextual_slice_budget(entity_types: List[str], window: int) -> int:
    """UTF-8 bytes one slice may take so its prompt and answer fit ``window`` tokens.

    ``window`` is the model's real window (``ollama_context_window``). A
    byte-level tokenizer never makes more tokens than the text has bytes,
    so a prompt of N bytes is at most N tokens, plus the model's chat
    template (``_CHAT_TEMPLATE_MARGIN_TOKENS``). Counting bytes rather than
    characters keeps dense scripts (three bytes a character for Han or
    Thai, four for emoji) inside the window that ASCII fills at its
    character limit. A window too small for a slice of ``_MIN_SLICE_BYTES``
    is refused.
    """
    overhead = len(PromptCatalog.semantic_pii_detection("", entity_types).encode("utf-8"))
    budget = window - OLLAMA_NUM_PREDICT - _CHAT_TEMPLATE_MARGIN_TOKENS - overhead - 1
    if budget < _MIN_SLICE_BYTES:
        raise _contextual_refusal("prompt_too_long")
    return budget


def _placed_reply_spans(
    items: List[dict], text: str, requested: tuple[str, ...]
) -> List[DetectedSpan]:
    """Place one reply's values in ``text``, or raise when none of them can be placed.

    Values left unplaced beside placed ones are only counted in the log,
    never quoted.
    """
    spans, unplaced, total = _reply_spans(items, text, requested)
    if total and unplaced == total:
        raise _contextual_refusal("unplaceable_reply")
    if unplaced:
        logger.warning("Contextual layer could not place %d of %d reported values", unplaced, total)
    return spans


def _placeholder_for_reference(
    anon_map: AnonymizationMap, refers_to: str, language: str
) -> Optional[str]:
    """Find the placeholder for ``refers_to``: exact key first, then normalized comparison."""
    from septum_core.text_utils import normalize_for_comparison

    placeholder = anon_map.entity_map.get(refers_to)
    if placeholder:
        return placeholder
    norm_ref = normalize_for_comparison(refers_to, language)
    for orig, ph in anon_map.entity_map.items():
        if normalize_for_comparison(orig, language) == norm_ref:
            return ph
    return None


def _register_pronoun(item: dict, anon_map: AnonymizationMap, language: str) -> bool:
    """Map one pronoun item onto its person placeholder; return whether it was registered."""
    from septum_core.anonymization_map import SANITIZER_STOPWORDS
    from septum_core.text_utils import normalize_for_comparison

    pronoun_text = item.get("text", "").strip()
    refers_to = item.get("refers_to", "").strip()
    if not pronoun_text or not refers_to:
        return False
    placeholder = _placeholder_for_reference(anon_map, refers_to, language)
    if not placeholder:
        return False
    norm_pronoun = normalize_for_comparison(pronoun_text, language)
    if len(norm_pronoun) <= 1 or norm_pronoun in SANITIZER_STOPWORDS:
        return False
    anon_map.blocklist.add(norm_pronoun)
    anon_map.token_to_placeholder[norm_pronoun] = placeholder
    return True


def _contextual_refusal(reason: str) -> SemanticLayerUnavailableError:
    """Log the failure kind and return the content-free error to raise."""
    logger.warning("Contextual layer got no usable answer from the local model: %s", reason)
    return SemanticLayerUnavailableError(f"The contextual detection layer could not run ({reason}).")


class OllamaSemanticAdapter:
    """Host-side :class:`SemanticDetectionPort` implementation backed by Ollama.

    Encapsulates every ``call_ollama_sync`` / ``PromptCatalog`` call that
    previously lived inside ``sanitizer.py``. The adapter reads runtime
    configuration from the attached :class:`AppSettings` row so the
    chat-model URL and model id stay consistent with the rest of the
    backend, while keeping the core detector completely network-free.
    """

    def __init__(self, settings: AppSettings, *, enabled: bool = True) -> None:
        self._settings = settings
        self._enabled = enabled

    def is_enabled(self) -> bool:
        return self._enabled

    def validate_candidates(
        self,
        *,
        text: str,
        candidate_spans: List[DetectedSpan],
        language: str,
        regulation_context: str,
    ) -> List[DetectedSpan]:
        """Validate candidate spans using Ollama's context and regulation awareness.

        Only a whole answer to a whole prompt may drop a candidate: a failed
        call, a reply cut at the token limit, or a prompt the server may have
        cut keeps every candidate, so validation fails toward masking more.
        """
        if not candidate_spans or not text:
            return list(candidate_spans)

        prompt = PromptCatalog.pii_validation_prompt(
            text=text,
            candidate_spans=_candidate_payload(text, candidate_spans),
            language=language,
            regulation_rules=regulation_context,
        )
        try:
            response = request_ollama_sync(
                prompt=prompt,
                base_url=self._settings.ollama_base_url,
                model=self._settings.ollama_deanon_model,
                allow_truncated=False,
            )
        except OllamaCallError as exc:
            logger.warning(
                "Ollama validation got no whole answer (%s); keeping all candidates", exc
            )
            return list(candidate_spans)
        if not response or not response.strip():
            logger.warning(
                "Ollama validation returned empty response; keeping all candidates"
            )
            return list(candidate_spans)

        validated_items = extract_json_array(response)
        if not validated_items:
            logger.warning(
                "Ollama validation parsed no spans; keeping all candidates"
            )
            return list(candidate_spans)

        validated_spans = [
            span
            for span in (_matching_candidate(item, candidate_spans) for item in validated_items)
            if span is not None
        ]
        if not validated_spans:
            logger.warning(
                "Ollama validation matched no spans; keeping all candidates"
            )
            return list(candidate_spans)

        return validated_spans

    def detect_aliases(self, *, normalized_text: str) -> List[DetectedSpan]:
        """Call local Ollama to detect PII (aliases, nicknames)."""
        if not normalized_text or _is_numeric_heavy(normalized_text):
            return []

        prompt = PromptCatalog.sanitizer_alias_layer(normalized_text)
        response = call_ollama_sync(
            prompt=prompt,
            base_url=self._settings.ollama_base_url,
            model=self._settings.ollama_deanon_model,
        )
        if not response or not response.strip():
            logger.warning("Layer 3 Ollama returned empty response")
        items = extract_json_array(response)
        logger.debug("Layer 3 Ollama parsed %d JSON items", len(items))

        spans = (_alias_span(item, normalized_text) for item in items)
        return [span for span in spans if span is not None]

    def detect_semantic(
        self,
        *,
        normalized_text: str,
        entity_types: List[str],
    ) -> List[DetectedSpan]:
        """Call local Ollama to detect contextual entity types, slice by slice.

        Long text is cut by :func:`.contextual_placement._contextual_slices`,
        sized so no prompt can exceed the model's window; the spans of every
        slice get absolute offsets and are deduplicated. A value the model
        names is masked at each occurrence (see
        :func:`.contextual_placement._placed_offsets`) in its slice and the
        overlap on either side of it, so a value the model completed across
        a cut is still placed. A failed call, a truncated reply, a reply
        that is neither exactly ``[]`` nor a usable array, or one none of
        whose values can be placed raises
        :class:`SemanticLayerUnavailableError` so the text is refused rather
        than passed on unchecked.
        """
        if not normalized_text.strip() or not entity_types:
            return []
        requested = tuple(dict.fromkeys(entity_types))
        try:
            window = ollama_context_window(
                self._settings.ollama_base_url, self._settings.ollama_deanon_model
            )
        except OllamaCallError as exc:
            raise _contextual_refusal(str(exc)) from exc
        budget = _contextual_slice_budget(entity_types, window)
        spans: dict[tuple[int, int, str], DetectedSpan] = {}
        for offset, text_slice in _contextual_slices(normalized_text, budget):
            items = self._contextual_items(text_slice, entity_types)
            if not items:
                continue
            low = max(0, offset - _CONTEXTUAL_SLICE_OVERLAP)
            high = offset + len(text_slice) + _CONTEXTUAL_SLICE_OVERLAP
            for span in _placed_reply_spans(items, normalized_text[low:high], requested):
                absolute = DetectedSpan(
                    start=span.start + low,
                    end=span.end + low,
                    entity_type=span.entity_type,
                    score=span.score,
                )
                spans.setdefault((absolute.start, absolute.end, absolute.entity_type), absolute)
        return sorted(spans.values(), key=lambda s: (s.start, s.end, s.entity_type))

    def _contextual_items(self, text_slice: str, entity_types: List[str]) -> List[dict]:
        """Return the items the model reports for one slice, or raise the coded refusal."""
        prompt = PromptCatalog.semantic_pii_detection(text_slice, entity_types)
        try:
            response = request_ollama_sync(
                prompt=prompt,
                base_url=self._settings.ollama_base_url,
                model=self._settings.ollama_deanon_model,
                allow_truncated=False,
            )
        except OllamaCallError as exc:
            raise _contextual_refusal(str(exc)) from exc
        items = reply_items(response)
        if items is None:
            raise _contextual_refusal("unparsable_reply")
        return items

    def resolve_coreference(
        self,
        *,
        normalized_text: str,
        anon_map: AnonymizationMap,
        language: str,
    ) -> int:
        """Use Ollama to identify pronouns referring to known person entities."""
        person_entities = [
            {"name": orig, "placeholder": ph}
            for orig, ph in anon_map.entity_map.items()
            if any(bt in ph for bt in _PERSON_PLACEHOLDER_TYPES)
        ]
        if not person_entities:
            return 0

        prompt = PromptCatalog.pronoun_coreference_resolution(
            normalized_text=normalized_text,
            known_persons=person_entities,
            language=language,
        )
        response = call_ollama_sync(
            prompt=prompt,
            base_url=self._settings.ollama_base_url,
            model=self._settings.ollama_deanon_model,
        )
        if not response or not response.strip():
            return 0

        resolved_count = sum(
            1
            for item in extract_json_array(response)
            if _register_pronoun(item, anon_map, language)
        )
        if resolved_count > 0:
            logger.debug(
                "Pronoun coreference resolved %d pronouns for document_id=%s",
                resolved_count,
                anon_map.document_id,
            )
        return resolved_count


def _settings_to_core_config(
    settings: AppSettings,
    enable_ollama_layer: Optional[bool] = None,
) -> SeptumCoreConfig:
    """Project :class:`AppSettings` onto the core :class:`SeptumCoreConfig`."""
    use_semantic_detection = (
        settings.use_ollama_layer if enable_ollama_layer is None else enable_ollama_layer
    )
    return SeptumCoreConfig(
        use_presidio_layer=settings.use_presidio_layer,
        use_ner_layer=settings.use_ner_layer,
        use_semantic_validation=settings.use_ollama_validation_layer,
        use_semantic_detection=bool(use_semantic_detection),
        use_semantic_contextual_detection=getattr(
            settings, "use_ollama_semantic_layer", False
        ),
        ner_model_overrides=dict(settings.ner_model_overrides or {}),
    )


class PIISanitizer(Detector):
    """Backend-side PII sanitizer that injects the Ollama semantic adapter.

    Accepts the legacy ``AppSettings`` constructor signature so all
    historical call sites (``PIISanitizer(settings=app_settings,
    policy=policy)``, ``PIISanitizer(..., enable_ollama_layer=False)``)
    keep working unchanged.
    """

    def __init__(
        self,
        settings: AppSettings,
        ner_registry: Optional[NERModelRegistry] = None,
        policy: Optional[ComposedPolicy] = None,
        enable_ollama_layer: Optional[bool] = None,
    ) -> None:
        self._settings = settings
        core_config = _settings_to_core_config(settings, enable_ollama_layer)
        semantic_port: SemanticDetectionPort = OllamaSemanticAdapter(
            settings=settings,
            enabled=(
                core_config.use_semantic_detection
                or core_config.use_semantic_validation
                or core_config.use_semantic_contextual_detection
            ),
        )
        super().__init__(
            config=core_config,
            ner_registry=ner_registry,
            policy=policy,
            semantic_port=semantic_port,
        )

    def _ollama_validate_pii_candidates(
        self,
        text: str,
        candidate_spans: List[DetectedSpan],
        language: str,
    ) -> List[DetectedSpan]:
        """Legacy alias for :meth:`Detector._semantic_validate_candidates`.

        Preserves the pre-refactor private entry point so tests and
        other consumers that reach into the sanitizer directly keep
        working without being rewritten.
        """
        return self._semantic_validate_candidates(
            text=text,
            candidate_spans=candidate_spans,
            language=language,
        )


__all__ = [
    "AnonymizationMap",
    "BaseCustomRecognizer",
    "CPFRecognizer",
    "CookieIDRecognizer",
    "CoordinatesRecognizer",
    "CreditCardNumberRecognizer",
    "DateOfBirthRecognizer",
    "Detector",
    "DetectedSpan",
    "DeviceIDRecognizer",
    "DriversLicenseRecognizer",
    "ExtendedPhoneRecognizer",
    "HeuristicPersonNameRecognizer",
    "LicensePlateRecognizer",
    "MACAddressRecognizer",
    "OllamaSemanticAdapter",
    "PIISanitizer",
    "PassportNumberRecognizer",
    "ResolvedSpan",
    "SSNRecognizer",
    "SanitizeResult",
    "StructuralAddressRecognizer",
    "TaxIDRecognizer",
    "URLRecognizer",
    "ValidatedIBANRecognizer",
    "_HIGH_PRIORITY_ENTITY_TYPES",
    "_CONTEXTUAL_ENTITY_TYPES",
    "_NER_LAYER_ENTITY_TYPES",
    "_OLLAMA_VALIDATION_PASSTHROUGH_TYPES",
    "_PARENT_TYPE_COVERAGE",
    "_PRESIDIO_DEFAULT_LANGUAGE",
    "_PRESIDIO_ENTITY_ALIASES",
    "_PRESIDIO_REVERSE_ALIASES",
]
