from __future__ import annotations

"""
PII sanitization pipeline for Septum.

This module implements a multi-layer PII detection stack:
1. Presidio AnalyzerEngine with custom recognizers (Layer 1).
2. HuggingFace NER models selected via NERModelRegistry (Layer 2).
3. Optional Ollama validation layer (Layer 3) - filters false positives using
   regulation-aware, context-aware LLM judgment.
4. Optional Ollama alias/nickname layer (Layer 4) - detects indirect references.

The actual string replacements are performed via AnonymizationMap, which
ensures that placeholders are stable and that a token-level blocklist
is applied as a final safety net.
"""

from dataclasses import dataclass
import logging
import re
from typing import Any, List, Optional

from presidio_analyzer import AnalyzerEngine, EntityRecognizer, RecognizerResult

from .anonymization_map import AnonymizationMap, SANITIZER_STOPWORDS
from .ner_model_registry import NERModelRegistry
from .non_pii_filter import NonPiiFilter, SpanView
from .ollama_client import extract_json_array, call_ollama_sync
from .national_ids import IBANValidator, TCKNValidator
from .policy_composer import ComposedPolicy
from .prompts import PromptCatalog
from ..models.settings import AppSettings
from ..utils.text_utils import normalize_unicode, normalize_for_comparison

logger = logging.getLogger(__name__)

_HIGH_PRIORITY_ENTITY_TYPES: set[str] = {
    "PHONE_NUMBER",
    "NATIONAL_ID",
    "IBAN",
    "IBAN_CODE",
}


@dataclass
class DetectedSpan:
    """Represents a PII span detected by any layer of the sanitizer."""

    start: int
    end: int
    entity_type: str
    score: float


@dataclass
class SanitizeResult:
    """Result of applying the PII sanitizer to an input string."""

    sanitized_text: str
    entity_count: int


class ExtendedPhoneRecognizer(EntityRecognizer):
    """Presidio recognizer for phone numbers with international format support.
    
    Detects phone numbers with country codes and various formatting patterns.
    Pattern accommodates multiple regional formats without hardcoding countries.
    """

    def __init__(self) -> None:
        super().__init__(
            supported_entities=["PHONE_NUMBER"],
            supported_language="en",
        )
        # Pattern: optional country code (+XX or 0), followed by 10 digits with flexible separators
        self._pattern = re.compile(
            r"\b(?:\+?\d{1,3}\s*)?(?:0\s*)?(?:\d{3})[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}\b"
        )

    def analyze(  # type: ignore[override]
        self,
        text: str,
        entities: Optional[List[str]] = None,
        nlp_artifacts: Optional[object] = None,
    ) -> List[RecognizerResult]:
        if entities and not set(entities).intersection(self.supported_entities):
            return []

        results: List[RecognizerResult] = []
        for match in self._pattern.finditer(text):
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


class ValidatedNationalIDRecognizer(EntityRecognizer):
    """Presidio recognizer for algorithmically validated national ID numbers.
    
    Uses pluggable validator for checksum-based validation.
    Default validator handles 11-digit IDs with specific checksum algorithm.
    Validator can be swapped to support different countries' ID formats.
    """

    def __init__(self, validator: Optional[TCKNValidator] = None) -> None:
        super().__init__(
            supported_entities=["NATIONAL_ID"],
            supported_language="en",
        )
        self._validator = validator or TCKNValidator()
        self._pattern = re.compile(r"\b[1-9]\d{10}\b")

    def analyze(  # type: ignore[override]
        self,
        text: str,
        entities: Optional[List[str]] = None,
        nlp_artifacts: Optional[object] = None,
    ) -> List[RecognizerResult]:
        if entities and not set(entities).intersection(self.supported_entities):
            return []

        results: List[RecognizerResult] = []
        for match in self._pattern.finditer(text):
            candidate = match.group(0)
            if not self._validator.validate(candidate):
                continue
            start, end = match.span()
            results.append(
                RecognizerResult(
                    entity_type="NATIONAL_ID",
                    start=start,
                    end=end,
                    score=0.95,
                )
            )
        return results


class ValidatedIBANRecognizer(EntityRecognizer):
    """Presidio recognizer for IBAN values using ISO 7064 algorithmic validation.
    
    Supports all country prefixes globally through ISO 7064 MOD 97-10 checksum.
    No country-specific logic in this class - validator handles all variations.
    """

    def __init__(self, validator: Optional[IBANValidator] = None) -> None:
        super().__init__(
            supported_entities=["IBAN"],
            supported_language="en",
        )
        self._validator = validator or IBANValidator()
        self._pattern = re.compile(r"\b[A-Za-z]{2}[0-9A-Za-z]{13,32}\b")

    def analyze(  # type: ignore[override]
        self,
        text: str,
        entities: Optional[List[str]] = None,
        nlp_artifacts: Optional[object] = None,
    ) -> List[RecognizerResult]:
        if entities and not set(entities).intersection(self.supported_entities):
            return []

        results: List[RecognizerResult] = []
        for match in self._pattern.finditer(text):
            candidate = match.group(0)
            if not self._validator.validate(candidate):
                continue
            start, end = match.span()
            results.append(
                RecognizerResult(
                    entity_type="IBAN",
                    start=start,
                    end=end,
                    score=0.96,
                )
            )
        return results


class HeuristicPersonNameRecognizer(EntityRecognizer):
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
        if entities and not set(entities).intersection(self.supported_entities):
            return []

        results: List[RecognizerResult] = []
        seen_spans: set[tuple[int, int]] = set()

        # Single-word tokens on their own line after a colon (e.g., "Name:\nSmith\n")
        for match in self._pattern_after_label.finditer(text):
            group_start = match.start(1)
            group_end = match.end(1)
            matched_text = text[group_start:group_end]
            
            # Require first character to be uppercase
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

class PIISanitizer:
    """High-level orchestrator for multi-layer PII detection and masking."""

    def __init__(
        self,
        settings: AppSettings,
        ner_registry: Optional[NERModelRegistry] = None,
        policy: Optional[ComposedPolicy] = None,
        enable_ollama_layer: Optional[bool] = None,
    ) -> None:
        self._settings = settings
        self._enable_ollama_layer = (
            settings.use_ollama_layer if enable_ollama_layer is None else enable_ollama_layer
        )
        self._ner_registry = ner_registry or NERModelRegistry()
        self._analyzer = AnalyzerEngine()
        # Increase SpaCy max_length on all underlying models to better handle long texts.
        # Presidio's default SpacyNlpEngine stores models in a dict: nlp_engine.nlp[language].
        try:
            nlp_engine = getattr(self._analyzer, "nlp_engine", None)
            nlp_store = getattr(nlp_engine, "nlp", None) if nlp_engine is not None else None
            # nlp_store is expected to be a dict[str, Language] for SpacyNlpEngine.
            if isinstance(nlp_store, dict):
                for model in nlp_store.values():
                    if hasattr(model, "max_length"):
                        current_max = getattr(model, "max_length", 0)
                        if current_max < 2_000_000:
                            model.max_length = 2_000_000
        except Exception:  # pragma: no cover - defensive, analyzer internals may change
            logger.debug("Could not adjust SpaCy max_length on AnalyzerEngine.nlp_engine")
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
        that ``analyze`` can limit detection to the active set.
        """
        registry = self._analyzer.registry
        for recognizer in policy.recognizers:
            registry.add_recognizer(recognizer)
        self._entity_types = list(policy.entity_types)
        self._non_pii_filter = NonPiiFilter.from_rules(policy.non_pii_rules)

    def _register_custom_recognizers(self) -> None:
        """Register project-specific Presidio recognizers on the analyzer registry."""
        registry = self._analyzer.registry
        registry.add_recognizer(ExtendedPhoneRecognizer())
        registry.add_recognizer(ValidatedNationalIDRecognizer())
        registry.add_recognizer(ValidatedIBANRecognizer())
        registry.add_recognizer(HeuristicPersonNameRecognizer())

    def sanitize(
        self,
        text: str,
        language: str,
        anon_map: AnonymizationMap,
    ) -> SanitizeResult:
        """Run the multi-layer sanitizer on the given text.

        Presidio is invoked with language='en' because the project uses a single
        default SpaCy pipeline; regex- and validator-based recognizers still apply.
        """
        if not text:
            return SanitizeResult(sanitized_text=text, entity_count=0)

        normalized_text = normalize_unicode(text)
        spans: List[DetectedSpan] = []

        if self._settings.use_presidio_layer:
            presidio_results = self._analyzer.analyze(
                text=normalized_text,
                language="en",
                entities=self._entity_types,
            )
            presidio_results = self._filter_presidio_results(
                presidio_results, normalized_text
            )
            spans.extend(self._from_presidio_results(presidio_results))

        if self._settings.use_ner_layer:
            ner_pipeline = self._ner_registry.get_pipeline(language)
            ner_results = ner_pipeline(normalized_text)
            spans.extend(self._from_ner_results(ner_results, normalized_text, language))

        if self._settings.use_ollama_validation_layer and spans:
            try:
                validated_spans = self._ollama_validate_pii_candidates(
                    text=normalized_text,
                    candidate_spans=spans,
                    language=language,
                )
                logger.debug(
                    "Ollama validation: %d candidates → %d validated spans",
                    len(spans),
                    len(validated_spans),
                )
                spans = validated_spans
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "Ollama validation layer failed, continuing with all candidates: %s", e
                )

        if self._enable_ollama_layer:
            try:
                ollama_spans = self._ollama_pii_detection(normalized_text)
                logger.debug(
                    "Layer 3 Ollama returned %d spans: %s",
                    len(ollama_spans),
                    [(s.start, s.end, normalized_text[s.start : s.end], s.entity_type) for s in ollama_spans],
                )
                spans.extend(ollama_spans)
            except Exception as e:  # noqa: BLE001
                logger.warning("Ollama PII layer failed, continuing without it: %s", e)

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
            # Map filtered views back to DetectedSpan objects.
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

        spans = self._deduplicate(spans)

        # Expand person-name spans to cover adjacent capitalized tokens so that
        # given-name-only detections are upgraded to full name blocks where
        # possible. This is language-agnostic and relies only on character
        # casing and token boundaries.
        spans = self._expand_person_name_spans(normalized_text, spans)

        spans = [
            s
            for s in spans
            if normalize_for_comparison(
                normalized_text[s.start : s.end].strip(), language
            )
            not in SANITIZER_STOPWORDS
        ]

        sanitized, count = self._apply_replacements(
            normalized_text, spans, anon_map, language
        )
        sanitized = anon_map.apply_blocklist(sanitized, language)
        self._log_low_confidence(spans)

        return SanitizeResult(sanitized_text=sanitized, entity_count=count)

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

            if self._settings.use_ner_layer and r.entity_type == "PERSON_NAME" and r.score < 0.7:
                continue

            if r.entity_type == "PHONE_NUMBER":
                span_text = text[r.start : r.end]
                digit_count = sum(1 for ch in span_text if ch.isdigit())
                has_dot_or_slash = "." in span_text or "/" in span_text
                if has_dot_or_slash and digit_count <= 8:
                    # Likely a date or similar numeric token, not a phone number.
                    continue

            filtered.append(r)
        return filtered

    def _ollama_validate_pii_candidates(
        self,
        text: str,
        candidate_spans: List[DetectedSpan],
        language: str,
    ) -> List[DetectedSpan]:
        """Validate candidate spans using Ollama's context and regulation awareness.

        This method sends the full text and all candidate spans to a local Ollama
        model, asking it to filter out false positives (general terms, job titles,
        city names in organizational context) and return only genuine PII according
        to active regulations. This provides an LLM-powered safety check that is
        language-agnostic and regulation-aware.
        """
        if not candidate_spans or not text:
            return candidate_spans

        regulation_rules = self._build_regulation_context()
        candidate_dicts = [
            {
                "text": text[span.start : span.end],
                "entity_type": span.entity_type,
                "start": span.start,
                "end": span.end,
                "score": span.score,
            }
            for span in candidate_spans
        ]

        prompt = PromptCatalog.pii_validation_prompt(
            text=text,
            candidate_spans=candidate_dicts,
            language=language,
            regulation_rules=regulation_rules,
        )

        response = call_ollama_sync(
            prompt=prompt,
            base_url=self._settings.ollama_base_url,
            model=self._settings.ollama_deanon_model,
        )

        if not response or not response.strip():
            logger.warning("Ollama validation returned empty response, keeping all candidates")
            return candidate_spans

        validated_items = extract_json_array(response)
        if not validated_items:
            logger.debug("Ollama validation returned no PII spans (filtered all candidates)")
            return []

        validated_spans: List[DetectedSpan] = []
        for item in validated_items:
            if not isinstance(item, dict):
                continue
            span_text = item.get("text")
            entity_type = item.get("entity_type")
            start = item.get("start")
            end = item.get("end")

            if span_text is None or entity_type is None or start is None or end is None:
                continue

            for original_span in candidate_spans:
                if (
                    original_span.start == start
                    and original_span.end == end
                    and original_span.entity_type == entity_type
                ):
                    validated_spans.append(original_span)
                    break

        return validated_spans

    def _build_regulation_context(self) -> str:
        """Build a human-readable summary of active regulation rules."""
        if self._entity_types is None or not self._entity_types:
            return "No specific regulations active (generic PII detection)."

        context_parts = [
            "Active entity types for PII detection:",
            ", ".join(sorted(set(self._entity_types))),
        ]
        return "\n".join(context_parts)

    def _ollama_pii_detection(self, normalized_text: str) -> List[DetectedSpan]:
        """Call local Ollama to detect PII (aliases, nicknames); return spans for Layer 4.

        Character positions are derived from the text field (Ollama start/end may not
        match when leading articles like 'The' are part of the alias).
        """
        if not normalized_text:
            return []
        # Heuristic short-circuit:
        # If the text is dominated by numeric or price-like lines (for example,
        # menus, inventories, or tabular price lists), skip the alias/nickname
        # layer entirely. Such content rarely contains nicknames or codenames
        # and running an LLM there tends to produce noisy pseudo-PII detections.
        lines = [ln.strip() for ln in normalized_text.splitlines() if ln.strip()]
        if lines:
            numeric_like = 0
            for line in lines:
                if not line:
                    continue
                tokens = line.split()
                if not tokens:
                    continue
                digit_tokens = 0
                for token in tokens:
                    if any(ch.isdigit() for ch in token):
                        digit_tokens += 1
                    # Currency-like symbols or explicit unit markers often appear
                    # in price lists and structured numeric content.
                    if any(ch in token for ch in ("$", "€", "£", "¥", "%")):
                        digit_tokens += 1
                if digit_tokens >= max(1, len(tokens) // 2):
                    numeric_like += 1
            if numeric_like / max(1, len(lines)) >= 0.4:
                logger.debug(
                    "Skipping Ollama alias layer for numeric-heavy structured text "
                    "(%d/%d lines classified as numeric-like).",
                    numeric_like,
                    len(lines),
                )
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
        spans_list: List[DetectedSpan] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            entity_type = item.get("entity_type") or item.get("type") or "ALIAS"
            if text is None or text == "":
                continue
            text_str = str(text).strip()
            _LEADING_ARTICLES = ("The ", "A ", "An ")
            start_idx = -1
            end_idx = -1
            
            # Case-insensitive search: Ollama may return capitalized names
            # while the original text contains lowercase variants
            text_lower = normalized_text.lower()
            search_lower = text_str.lower()
            
            for article in _LEADING_ARTICLES:
                extended = article + text_str
                extended_lower = extended.lower()
                idx = text_lower.find(extended_lower)
                if idx >= 0:
                    start_idx = idx
                    end_idx = idx + len(extended)
                    break
            if start_idx < 0:
                idx = text_lower.find(search_lower)
                if idx >= 0:
                    start_idx = idx
                    end_idx = idx + len(text_str)
            if start_idx >= 0:
                spans_list.append(
                    DetectedSpan(
                        start=start_idx,
                        end=end_idx,
                        entity_type=str(entity_type).upper().replace(" ", "_"),
                        score=0.85,
                    )
                )
        return spans_list

    @staticmethod
    def _from_presidio_results(
        results: List[RecognizerResult],
    ) -> List[DetectedSpan]:
        """Convert Presidio RecognizerResult objects to DetectedSpan."""
        spans: List[DetectedSpan] = []
        for r in results:
            entity_type = r.entity_type
            if entity_type == "IBAN_CODE":
                entity_type = "IBAN"

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
    def _from_ner_results(
        raw_results: List[dict],
        text: str,
        language: str = "en",
    ) -> List[DetectedSpan]:
        """Convert HuggingFace NER pipeline outputs to DetectedSpan.

        Uses a lower confidence threshold for PERSON_NAME and LOCATION when
        processing non-default locales, so that locale-specific models
        retain more detections for given names and place names that may have
        lower scores. ORGANIZATION_NAME keeps the stricter threshold.
        """
        lang_norm = (language or "en").strip().lower()
        threshold = 0.75 if lang_norm != "en" else 0.85
        spans: List[DetectedSpan] = []
        numeric_filtered_org = 0
        for item in raw_results:
            entity = item.get("entity_group") or item.get("entity")
            start = int(item["start"])
            end = int(item["end"])
            score = float(item.get("score", 0.0))
            if not entity:
                continue
            entity_type = PIISanitizer._map_ner_label(entity)
            if entity_type is None:
                continue

            if entity_type in {"PERSON_NAME", "LOCATION"}:
                if score < threshold:
                    continue
            elif entity_type == "ORGANIZATION_NAME":
                if score < 0.85:
                    continue

            # Additional heuristic for ORGANIZATION_NAME in numeric/price-like lines:
            # when the surrounding line is dominated by digits or currency/percentage
            # symbols, treat it as structured numeric content (for example menus,
            # inventory lists) and skip the span.
            if entity_type == "ORGANIZATION_NAME":
                line_start = text.rfind("\n", 0, start) + 1
                if line_start < 0:
                    line_start = 0
                line_end = text.find("\n", end)
                if line_end < 0:
                    line_end = len(text)
                line = text[line_start:line_end].strip()
                if line:
                    tokens = line.split()
                    numeric_like_tokens = 0
                    for token in tokens:
                        if any(ch.isdigit() for ch in token):
                            numeric_like_tokens += 1
                        if any(ch in token for ch in ("$", "€", "£", "¥", "%")):
                            numeric_like_tokens += 1
                    if numeric_like_tokens >= max(1, len(tokens) // 2):
                        numeric_filtered_org += 1
                        continue

            spans.append(
                DetectedSpan(
                    start=start,
                    end=end,
                    entity_type=entity_type,
                    score=score,
                )
            )
        if numeric_filtered_org:
            logger.debug(
                "Filtered %d ORGANIZATION_NAME spans in numeric-like lines from NER layer",
                numeric_filtered_org,
            )
        return spans

    @staticmethod
    def _map_ner_label(label: str) -> Optional[str]:
        """Map model-specific NER labels to global entity types."""
        upper = label.upper()
        if "PER" in upper or upper.startswith("B-PER") or upper.startswith("I-PER"):
            return "PERSON_NAME"
        # Organization entities from generic NER models tend to be noisy for
        # structured content (for example menus or product catalogs) and are
        # not strictly required for most privacy regulations. They are therefore
        # ignored at the NER layer and can still be supplied by regulation-
        # specific Presidio recognizers when needed.
        if "ORG" in upper:
            return None
        if "LOC" in upper:
            return "LOCATION"
        if "EMAIL" in upper:
            return "EMAIL_ADDRESS"
        return None

    @staticmethod
    def _deduplicate(spans: List[DetectedSpan]) -> List[DetectedSpan]:
        """Deduplicate overlapping spans with priority for sensitive identifiers.

        High-priority entity types (for example ``PHONE_NUMBER``, ``NATIONAL_ID``,
        ``IBAN``) always win over more generic entities such as PERSON or
        LOCATION when their spans overlap. Non-overlapping low-priority spans
        are still preserved.
        """
        if not spans:
            return []

        high_priority = [s for s in spans if s.entity_type in _HIGH_PRIORITY_ENTITY_TYPES]
        low_priority = [s for s in spans if s.entity_type not in _HIGH_PRIORITY_ENTITY_TYPES]

        def _dedup_simple(candidates: List[DetectedSpan]) -> List[DetectedSpan]:
            ordered = sorted(
                candidates,
                key=lambda s: (s.start, -(s.end - s.start), -s.score),
            )
            chosen: List[DetectedSpan] = []
            current_end = -1
            for span in ordered:
                if span.start >= current_end:
                    chosen.append(span)
                    current_end = span.end
            return chosen

        high_dedup = _dedup_simple(high_priority)

        filtered_low: List[DetectedSpan] = []
        for span in low_priority:
            overlaps_high = any(
                not (span.end <= h.start or span.start >= h.end) for h in high_dedup
            )
            if not overlaps_high:
                filtered_low.append(span)

        low_dedup = _dedup_simple(filtered_low)

        combined = sorted(high_dedup + low_dedup, key=lambda s: s.start)
        return combined

    @staticmethod
    def _expand_person_name_spans(
        text: str,
        spans: List[DetectedSpan],
    ) -> List[DetectedSpan]:
        """Expand PERSON_NAME spans to include adjacent capitalized tokens.

        When a PERSON_NAME span covers only part of a name (for example, a given
        name without the following surname), this helper inspects the immediate
        neighbouring tokens on both sides and extends the span to include them
        when they look like name tokens (letter-only, starting with uppercase).
        The heuristic is intentionally simple and language-agnostic.
        """
        if not spans or not text:
            return spans

        def _find_token_start(idx: int) -> int:
            while idx > 0 and not text[idx - 1].isspace():
                idx -= 1
            return idx

        def _find_token_end(idx: int) -> int:
            n = len(text)
            while idx < n and not text[idx].isspace():
                idx += 1
            return idx

        def _is_name_like_token(start: int, end: int) -> bool:
            token = text[start:end]
            if not token:
                return False
            if not token[0].isalpha() or not token[0].isupper():
                return False
            if any(ch.isdigit() or ch == "_" for ch in token):
                return False
            return True

        expanded: List[DetectedSpan] = []
        occupied_ranges = [(s.start, s.end) for s in spans]

        for span in spans:
            if span.entity_type != "PERSON_NAME":
                expanded.append(span)
                continue

            start = span.start
            end = span.end

            # Look right for a candidate surname.
            right = end
            n = len(text)
            while right < n and text[right].isspace():
                right += 1
            if right < n:
                right_end = _find_token_end(right)
                if _is_name_like_token(right, right_end):
                    overlaps = any(
                        not (right_end <= s_start or right >= s_end)
                        for s_start, s_end in occupied_ranges
                    )
                    if not overlaps:
                        end = right_end

            # Look left for a preceding name token.
            left = start
            while left > 0 and text[left - 1].isspace():
                left -= 1
            if left > 0:
                left_start = _find_token_start(left - 1)
                if _is_name_like_token(left_start, left):
                    overlaps = any(
                        not (left <= s_start or left_start >= s_end)
                        for s_start, s_end in occupied_ranges
                    )
                    if not overlaps:
                        start = left_start

            expanded.append(
                DetectedSpan(
                    start=start,
                    end=end,
                    entity_type=span.entity_type,
                    score=span.score,
                )
            )

        expanded_sorted = sorted(
            expanded, key=lambda s: (s.start, -(s.end - s.start), -s.score)
        )
        return expanded_sorted

    @staticmethod
    def _apply_replacements(
        text: str,
        spans: List[DetectedSpan],
        anon_map: AnonymizationMap,
        language: str,
    ) -> tuple[str, int]:
        """Replace detected spans with placeholders using the anonymization map."""
        if not spans:
            return text, 0

        parts: List[str] = []
        last_index = 0
        count = 0

        for span in sorted(spans, key=lambda s: s.start):
            if span.start < last_index:
                continue
            parts.append(text[last_index : span.start])
            original = text[span.start : span.end]
            # Skip empty or whitespace-only entities
            if not original or not original.strip():
                last_index = span.end
                continue
            placeholder = anon_map.add_entity(original, span.entity_type)
            parts.append(placeholder)
            last_index = span.end
            count += 1

        parts.append(text[last_index:])
        return "".join(parts), count

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

