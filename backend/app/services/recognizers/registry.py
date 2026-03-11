from __future__ import annotations

"""
Recognizer registry for Septum.

This module provides `RecognizerRegistry`, which is responsible for composing
Presidio recognizers from:
- Built-in regulation packs (for example GDPR, HIPAA, KVKK).
- User-defined `CustomRecognizer` records stored in the database.

The resulting recognizers are meant to be plugged into Presidio's
`AnalyzerEngine` as part of the sanitization pipeline.
"""

from dataclasses import dataclass
import importlib
import logging
import re
from typing import List, Sequence

from presidio_analyzer import EntityRecognizer, Pattern, PatternRecognizer, RecognizerResult

from ...models.regulation import CustomRecognizer as CustomRecognizerModel
from ...models.regulation import RegulationRuleset

from ..ollama_client import call_ollama_sync, extract_json_array, use_ollama_enabled

logger = logging.getLogger(__name__)


@dataclass
class LLMContextConfig:
    """Configuration payload for an LLM-backed custom recognizer."""

    name: str
    entity_type: str
    llm_prompt: str
    context_words: List[str]


class LLMContextRecognizer(EntityRecognizer):
    """
    Recognizer that delegates detection to a local Ollama model using the
    custom rule's llm_prompt. Used when detection_method='llm_prompt'.
    """

    def __init__(self, config: LLMContextConfig) -> None:
        super().__init__(
            supported_entities=[config.entity_type],
            supported_language="en",
        )
        self._config = config

    def analyze(  # type: ignore[override]
        self,
        text: str,
        entities: List[str] | None = None,
        nlp_artifacts: object | None = None,
    ) -> List[RecognizerResult]:
        """Run Ollama with the rule's llm_prompt and return RecognizerResult spans."""
        if entities and not set(entities).intersection(self.supported_entities):
            return []
        if not use_ollama_enabled() or not text or not self._config.llm_prompt:
            return []

        prompt = (
            "You are a PII detection assistant. For the following instruction, "
            "find all matching spans in the text. Return a JSON array of objects "
            "with keys: start, end, text, entity_type. Use entity_type "
            f"\"{self._config.entity_type}\". Only return the JSON array, nothing else.\n\n"
            f"Instruction: {self._config.llm_prompt}\n\nText:\n{text}"
        )
        response = call_ollama_sync(prompt=prompt)
        items = extract_json_array(response)
        results: List[RecognizerResult] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            start = item.get("start")
            end = item.get("end")
            span_text = item.get("text")
            if span_text is None or span_text == "":
                continue
            start_int: int
            end_int: int
            if isinstance(start, int) and isinstance(end, int):
                if 0 <= start < end <= len(text) and text[start:end].strip() == str(span_text).strip():
                    start_int, end_int = start, end
                else:
                    idx = text.find(str(span_text))
                    if idx < 0:
                        continue
                    start_int, end_int = idx, idx + len(str(span_text))
            else:
                idx = text.find(str(span_text))
                if idx < 0:
                    continue
                start_int, end_int = idx, idx + len(str(span_text))
            results.append(
                RecognizerResult(
                    entity_type=self._config.entity_type,
                    start=start_int,
                    end=end_int,
                    score=0.8,
                )
            )
        return results


class RecognizerRegistry:
    """
    Build Presidio recognizers for the active regulations and custom rules.

    The registry is deliberately stateless; callers pass in the active
    `RegulationRuleset` and `CustomRecognizer` objects, and the registry
    returns a flat list of `EntityRecognizer` instances.
    """

    def build(
        self,
        active_regs: Sequence[RegulationRuleset],
        custom_recognizers: Sequence[CustomRecognizerModel],
    ) -> List[EntityRecognizer]:
        """Return all recognizers for the given regulations and custom rules."""
        recognizers: List[EntityRecognizer] = []
        recognizers.extend(self._load_builtin_packs(active_regs))
        recognizers.extend(self._from_custom_recognizers(custom_recognizers))
        return recognizers

    def _load_builtin_packs(
        self,
        active_regs: Sequence[RegulationRuleset],
    ) -> List[EntityRecognizer]:
        """Load recognizers from built-in packs; package path is derived from __name__."""
        recognizers: List[EntityRecognizer] = []
        base_pkg = __name__.rsplit(".", 1)[0]
        for reg in active_regs:
            module_path = f"{base_pkg}.{reg.id}.recognizers"
            try:
                module = importlib.import_module(module_path)
            except ModuleNotFoundError:
                logger.debug(
                    "No recognizer pack found for regulation '%s' (module %s).",
                    reg.id,
                    module_path,
                )
                continue

            get_recognizers = getattr(module, "get_recognizers", None)
            if get_recognizers is None:
                logger.warning(
                    "Recognizer pack %s for regulation '%s' "
                    "does not define get_recognizers().",
                    module_path,
                    reg.id,
                )
                continue

            try:
                pack_recognizers = list(get_recognizers())
            except Exception:  # pragma: no cover - defensive logging
                logger.exception(
                    "Error while loading recognizers from pack %s.", module_path
                )
                continue

            recognizers.extend(pack_recognizers)

        return recognizers

    def _from_custom_recognizers(
        self,
        custom_recognizers: Sequence[CustomRecognizerModel],
    ) -> List[EntityRecognizer]:
        """Build Presidio recognizers from custom recognizer models."""
        recognizers: List[EntityRecognizer] = []
        for custom in custom_recognizers:
            if not custom.is_active:
                continue

            method = custom.detection_method.lower()
            if method == "regex" and custom.pattern:
                recognizer = self._build_regex_recognizer(custom)
                if recognizer is not None:
                    recognizers.append(recognizer)
            elif method == "keyword_list" and custom.keywords:
                recognizers.append(self._build_keyword_recognizer(custom))
            elif method == "llm_prompt" and custom.llm_prompt:
                recognizers.append(self._build_llm_recognizer(custom))
            else:
                logger.warning(
                    "Skipping custom recognizer %s (%s) due to incomplete configuration.",
                    custom.id,
                    custom.name,
                )
        return recognizers

    @staticmethod
    def _build_regex_recognizer(
        custom: CustomRecognizerModel,
    ) -> PatternRecognizer | None:
        """Build a PatternRecognizer from a regex-based custom recognizer."""
        try:
            compiled = re.compile(custom.pattern or "")
        except re.error:
            logger.warning(
                "Invalid regex pattern for custom recognizer %s (%s); skipping.",
                custom.id,
                custom.name,
            )
            return None

        pattern = Pattern(
            name=custom.name,
            regex=compiled.pattern,
            score=0.6,
        )
        return PatternRecognizer(
            supported_entity=custom.entity_type,
            patterns=[pattern],
            name=custom.name,
            supported_language="en",
        )

    @staticmethod
    def _build_keyword_recognizer(
        custom: CustomRecognizerModel,
    ) -> PatternRecognizer:
        """Build a PatternRecognizer from a keyword-list custom recognizer."""
        keywords: List[str] = list(custom.keywords or [])
        escaped = [re.escape(k) for k in keywords if k]
        if not escaped:
            raise ValueError("Keyword recognizer requires at least one keyword.")

        pattern = Pattern(
            name=f"{custom.name}_keywords",
            regex=rf"\\b(?:{'|'.join(escaped)})\\b",
            score=0.7,
        )
        return PatternRecognizer(
            supported_entity=custom.entity_type,
            patterns=[pattern],
            name=custom.name,
            supported_language="en",
        )

    @staticmethod
    def _build_llm_recognizer(
        custom: CustomRecognizerModel,
    ) -> LLMContextRecognizer:
        """Build an LLMContextRecognizer from an llm_prompt custom recognizer."""
        config = LLMContextConfig(
            name=custom.name,
            entity_type=custom.entity_type,
            llm_prompt=custom.llm_prompt or "",
            context_words=list(custom.context_words or []),
        )
        return LLMContextRecognizer(config)

