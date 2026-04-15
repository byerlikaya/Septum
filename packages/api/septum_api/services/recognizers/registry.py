from __future__ import annotations

"""
Backend-side recognizer registry.

Wraps :class:`septum_core.recognizers.registry.RecognizerRegistry` and
injects :class:`LLMContextRecognizer` for custom rules whose
``detection_method`` is ``llm_prompt``. The LLM recognizer itself is
kept on the backend side because it depends on the local Ollama HTTP
client, which is not available inside the air-gapped core package.
"""

import logging
from dataclasses import dataclass
from typing import List, Optional

from presidio_analyzer import EntityRecognizer, RecognizerResult

from septum_core.recognizers.registry import RecognizerRegistry as _CoreRecognizerRegistry
from septum_core.regulations.models import CustomRecognizerLike

from ..ollama_client import call_ollama_sync, extract_json_array, use_ollama_enabled
from ..prompts import PromptCatalog

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

        prompt = PromptCatalog.llm_custom_recognizer_prompt(
            entity_type=self._config.entity_type,
            instruction=self._config.llm_prompt,
            text=text,
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


def _build_llm_recognizer_from_custom(
    custom: CustomRecognizerLike,
) -> Optional[EntityRecognizer]:
    """Host factory supplied to :class:`_CoreRecognizerRegistry` for llm_prompt rules."""
    if not custom.llm_prompt:
        return None
    config = LLMContextConfig(
        name=custom.name,
        entity_type=custom.entity_type,
        llm_prompt=custom.llm_prompt or "",
        context_words=list(custom.context_words or []),
    )
    return LLMContextRecognizer(config)


class RecognizerRegistry(_CoreRecognizerRegistry):
    """Backend registry that pre-wires the Ollama-backed LLM factory."""

    def __init__(self) -> None:
        super().__init__(llm_recognizer_factory=_build_llm_recognizer_from_custom)


__all__ = [
    "LLMContextConfig",
    "LLMContextRecognizer",
    "RecognizerRegistry",
]
