from __future__ import annotations

"""
PII sanitization pipeline for Septum.

This module implements a three-layer PII detection stack:
1. Presidio AnalyzerEngine with custom recognizers (Layer 1).
2. HuggingFace NER models selected via NERModelRegistry (Layer 2).
3. Optional Ollama-based context-aware layer (Layer 3, placeholder hook).

The actual string replacements are performed via AnonymizationMap, which
ensures that placeholders are stable and that a token-level blocklist
is applied as a final safety net.
"""

from dataclasses import dataclass
import logging
import re
from typing import List, Optional

from presidio_analyzer import AnalyzerEngine, EntityRecognizer, RecognizerResult

from .anonymization_map import AnonymizationMap, SANITIZER_STOPWORDS
from .ner_model_registry import NERModelRegistry
from .non_pii_filter import NonPiiFilter, SpanView
from .ollama_client import extract_json_array, call_ollama_sync
from .national_ids import IBANValidator, TCKNValidator
from .policy_composer import ComposedPolicy
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
        # Match two or more consecutive letter-only tokens where each token
        # begins with an uppercase letter. Uses Unicode-aware character
        # classes and remains free of language-specific terms.
        self._pattern = re.compile(
            r"\b[^\W\d_][^\W\d_]+(?:\s+[^\W\d_][^\W\d_]+)+\b", re.UNICODE
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
                    entity_type="PERSON_NAME",
                    start=start,
                    end=end,
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
    ) -> None:
        self._settings = settings
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
        English SpaCy pipeline; regex- and validator-based recognizers still apply.
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
            spans.extend(self._from_presidio_results(presidio_results))

        if self._settings.use_ner_layer:
            ner_pipeline = self._ner_registry.get_pipeline(language)
            ner_results = ner_pipeline(normalized_text)
            spans.extend(self._from_ner_results(ner_results))

        if self._settings.use_ollama_layer:
            ollama_spans = self._ollama_pii_detection(normalized_text)
            logger.debug(
                "Layer 3 Ollama returned %d spans: %s",
                len(ollama_spans),
                [(s.start, s.end, normalized_text[s.start : s.end], s.entity_type) for s in ollama_spans],
            )
            spans.extend(ollama_spans)

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

    def _ollama_pii_detection(self, normalized_text: str) -> List[DetectedSpan]:
        """Call local Ollama to detect PII (aliases, nicknames); return spans for Layer 1/2 merge.

        Character positions are derived from the text field (Ollama start/end may not
        match when leading articles like 'The' are part of the alias).
        """
        if not normalized_text:
            return []
        system_part = (
            "You are a PII detection assistant. Your ONLY job is to find "
            "entities that normal NER models miss: nicknames, aliases, codenames, "
            "indirect references to people, organizations referred to by informal names. "
            "Return ONLY valid JSON, no explanation. "
            "IMPORTANT: Always include leading articles (The, A, An) if they are "
            "part of the nickname or alias. For example 'The Big Fish' should be "
            "returned as 'The Big Fish', not 'Big Fish'."
        )
        user_part = (
            "Find all aliases, nicknames, and indirect person references in this text. "
            'Return JSON array: [{"text": "...", "start": N, "end": N, "type": "ALIAS"}]. '
            "If nothing found return [].\n\nText:\n"
            f"{normalized_text}"
        )
        prompt = f"System: {system_part}\n\nUser: {user_part}"
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
            for article in _LEADING_ARTICLES:
                extended = article + text_str
                idx = normalized_text.find(extended)
                if idx >= 0:
                    start_idx = idx
                    end_idx = idx + len(extended)
                    break
            if start_idx < 0:
                idx = normalized_text.find(text_str)
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
    def _from_ner_results(raw_results: List[dict]) -> List[DetectedSpan]:
        """Convert HuggingFace NER pipeline outputs to DetectedSpan.
        
        Applies stricter confidence thresholds for generic entity types
        (PERSON_NAME, ORGANIZATION_NAME, LOCATION) to avoid false positives
        on common nouns and general terms.
        """
        spans: List[DetectedSpan] = []
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
            
            # Stricter threshold for generic entity types to reduce false positives
            if entity_type in {"PERSON_NAME", "ORGANIZATION_NAME", "LOCATION"}:
                if score < 0.85:
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
        """Map model-specific NER labels to global entity types."""
        upper = label.upper()
        if "PER" in upper or upper.startswith("B-PER") or upper.startswith("I-PER"):
            return "PERSON_NAME"
        if "ORG" in upper:
            return "ORGANIZATION_NAME"
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

