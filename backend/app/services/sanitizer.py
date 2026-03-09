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

from .anonymization_map import AnonymizationMap
from .ner_model_registry import NERModelRegistry
from .national_ids import IBANValidator, TCKNValidator
from ..models.settings import AppSettings
from ..utils.text_utils import normalize_unicode

logger = logging.getLogger(__name__)

# Entity types which should take precedence over more generic detections when
# spans overlap. These are typically high-sensitivity identifiers where false
# negatives are less acceptable than over-masking surrounding context.
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


class TurkishPhoneRecognizer(EntityRecognizer):
    """Presidio recognizer for Turkish phone numbers."""

    def __init__(self) -> None:
        super().__init__(
            supported_entities=["PHONE_NUMBER"],
            # Presidio in this project is currently configured with an English
            # SpaCy model only. We therefore register this recognizer under
            # 'en' and route non-English texts through the English pipeline.
            supported_language="en",
        )
        # Accept common Turkish mobile/landline formats, with optional +90 / 0.
        self._pattern = re.compile(
            r"\b(?:\+?90\s*)?(?:0\s*)?(?:\d{3})[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}\b"
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


class TCKNEntityRecognizer(EntityRecognizer):
    """Presidio recognizer for Turkish national ID numbers (TCKN)."""

    def __init__(self, validator: Optional[TCKNValidator] = None) -> None:
        super().__init__(
            supported_entities=["NATIONAL_ID"],
            # See note in TurkishPhoneRecognizer: we currently bind all
            # Presidio recognizers to the English pipeline.
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


class IBANEntityRecognizer(EntityRecognizer):
    """Presidio recognizer for IBAN values using algorithmic validation."""

    def __init__(self, validator: Optional[IBANValidator] = None) -> None:
        super().__init__(
            supported_entities=["IBAN"],
            # IBAN structure is language-agnostic; we still bind it to the
            # English pipeline for compatibility with the current Presidio
            # configuration.
            supported_language="en",
        )
        self._validator = validator or IBANValidator()
        # Basic structural pattern: country code + 13–32 alphanumerics.
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


class PIISanitizer:
    """High-level orchestrator for multi-layer PII detection and masking."""

    def __init__(
        self,
        settings: AppSettings,
        ner_registry: Optional[NERModelRegistry] = None,
    ) -> None:
        self._settings = settings
        self._ner_registry = ner_registry or NERModelRegistry()

        # Base Presidio analyzer with custom recognizers.
        self._analyzer = AnalyzerEngine()
        self._register_custom_recognizers()

    def _register_custom_recognizers(self) -> None:
        """Register Turkish-specific recognizers on the analyzer registry."""
        registry = self._analyzer.registry
        registry.add_recognizer(TurkishPhoneRecognizer())
        registry.add_recognizer(TCKNEntityRecognizer())
        registry.add_recognizer(IBANEntityRecognizer())

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

        # Layer 1: Presidio AnalyzerEngine.
        if self._settings.use_presidio_layer:
            # Presidio is wired with an English SpaCy model. To avoid runtime
            # errors for languages without dedicated models (for example "tr"),
            # we always analyze using the "en" pipeline here. Regex- and
            # validator-based recognizers (such as Turkish phone, TCKN, IBAN)
            # remain fully effective under this configuration.
            presidio_results = self._analyzer.analyze(
                text=normalized_text,
                language="en",
            )
            spans.extend(self._from_presidio_results(presidio_results))

        # Layer 2: HuggingFace NER.
        if self._settings.use_ner_layer:
            ner_pipeline = self._ner_registry.get_pipeline(language)
            ner_results = ner_pipeline(normalized_text)
            spans.extend(self._from_ner_results(ner_results))

        # Layer 3: Ollama-based context-aware detection.
        # The concrete LLMContextRecognizer lives in the recognizer registry and
        # is wired through settings; here we only leave a placeholder hook for
        # future integration, to keep this class decoupled from any LLM client.
        if self._settings.use_ollama_layer:
            logger.debug("Ollama layer is enabled, but no LLM recognizer is wired yet.")

        # Deduplicate and apply anonymization map.
        spans = self._deduplicate(spans)
        sanitized, count = self._apply_replacements(
            normalized_text, spans, anon_map, language
        )
        sanitized = anon_map.apply_blocklist(sanitized, language)
        self._log_low_confidence(spans)

        return SanitizeResult(sanitized_text=sanitized, entity_count=count)

    @staticmethod
    def _from_presidio_results(
        results: List[RecognizerResult],
    ) -> List[DetectedSpan]:
        """Convert Presidio RecognizerResult objects to DetectedSpan."""
        spans: List[DetectedSpan] = []
        for r in results:
            # Normalize certain entity labels to match the global master list.
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
        """Convert HuggingFace NER pipeline outputs to DetectedSpan."""
        spans: List[DetectedSpan] = []
        for item in raw_results:
            entity = item.get("entity_group") or item.get("entity")
            start = int(item["start"])
            end = int(item["end"])
            score = float(item.get("score", 0.0))
            if not entity:
                continue
            # Map model-specific labels to generic entity types when possible.
            entity_type = PIISanitizer._map_ner_label(entity)
            if entity_type is None:
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
        # Simple heuristics; these can be extended per model family.
        if "PER" in upper or upper.startswith("B-PER") or upper.startswith("I-PER"):
            return "PERSON_NAME"
        if "ORG" in upper:
            return "ORGANIZATION_NAME"
        if "LOC" in upper:
            return "LOCATION"
        if "EMAIL" in upper:
            return "EMAIL_ADDRESS"
        # Unknown label: let it fall through.
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

        # Filter out low-priority spans that overlap with any high-priority span.
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

