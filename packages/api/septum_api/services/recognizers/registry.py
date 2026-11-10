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
import re
from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Tuple

from presidio_analyzer import EntityRecognizer, RecognizerResult
from septum_core.recognizers.registry import (
    RecognizerRegistry as _CoreRecognizerRegistry,
)
from septum_core.regulations.models import CustomRecognizerLike

from ...errors import CustomRuleUnavailableError
from ..ollama_client import (
    OllamaCallError,
    extract_json_array,
    request_ollama_sync,
    use_ollama_enabled,
)
from ..prompts import PromptCatalog

logger = logging.getLogger(__name__)


@dataclass
class LLMContextConfig:
    """Configuration payload for an LLM-backed custom recognizer."""

    name: str
    entity_type: str
    llm_prompt: str
    context_words: List[str]
    rule_id: Optional[int] = None
    base_url: Optional[str] = None
    model: Optional[str] = None


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
        try:
            response = request_ollama_sync(
                prompt=prompt,
                base_url=self._config.base_url,
                model=self._config.model,
                allow_truncated=False,
            )
        except OllamaCallError as exc:
            raise self._refusal(str(exc)) from exc
        items = _reply_items(response)
        if items is None:
            raise self._refusal("unparsable_reply")
        results: List[RecognizerResult] = []
        for item in items:
            span = _locate_span(item, text)
            if span is None:
                continue
            results.append(
                RecognizerResult(
                    entity_type=self._config.entity_type,
                    start=span[0],
                    end=span[1],
                    score=0.8,
                )
            )
        return results

    def _refusal(self, reason: str) -> CustomRuleUnavailableError:
        """Log the failed rule by id and name and return the error to raise."""
        logger.warning(
            "Custom rule %s (%s) got no usable answer from the local model: %s",
            self._config.rule_id,
            self._config.name,
            reason,
        )
        return CustomRuleUnavailableError(
            f"Custom detection rule {self._config.rule_id} could not run ({reason})."
        )


_EMPTY_REPLY_RE = re.compile(r"(?:```(?:json)?\s*)?\[\s*\]\s*(?:```)?", re.IGNORECASE)
"""A whole reply that is an empty JSON array, optionally fenced: the model found nothing."""


def _reply_items(response: str) -> Optional[List[dict]]:
    """Return the span items of a rule reply, or ``None`` when it cannot be trusted.

    Only a reply that is exactly an empty array (optionally fenced) means
    "found nothing". String items are taken as span texts. A reply with no
    item that is a non-empty string or a dict with a non-empty ``text`` is
    unparsable, so a malformed answer never passes as an empty one.
    """
    if _EMPTY_REPLY_RE.fullmatch(response.strip()):
        return []
    items: List[dict] = []
    for item in extract_json_array(response):
        if isinstance(item, str):
            item = {"text": item}
        if isinstance(item, dict):
            items.append(item)
    if not any(item.get("text") not in (None, "") for item in items):
        return None
    return items


def _locate_span(item: dict, text: str) -> Optional[Tuple[int, int]]:
    """Return the ``(start, end)`` of an LLM span in ``text``, or ``None`` when it cannot be placed.

    The offsets the model reported are trusted only when they are in range
    and cover the reported text; otherwise the first occurrence of the text
    is used.
    """
    span_text = item.get("text")
    if span_text is None or span_text == "":
        return None
    needle = str(span_text)
    start = item.get("start")
    end = item.get("end")
    if (
        isinstance(start, int)
        and isinstance(end, int)
        and 0 <= start < end <= len(text)
        and text[start:end].strip() == needle.strip()
    ):
        return start, end
    idx = text.find(needle)
    if idx < 0:
        return None
    return idx, idx + len(needle)


def _llm_recognizer_factory(
    settings: Any | None,
) -> Callable[[CustomRecognizerLike], Optional[EntityRecognizer]]:
    """Return the host factory for llm_prompt rules, bound to the Ollama URL and model of ``settings``.

    Without ``settings`` the recognizer uses the client's environment defaults.
    """
    base_url = getattr(settings, "ollama_base_url", None)
    model = getattr(settings, "ollama_deanon_model", None)

    def _build(custom: CustomRecognizerLike) -> Optional[EntityRecognizer]:
        if not custom.llm_prompt:
            return None
        return LLMContextRecognizer(
            LLMContextConfig(
                name=custom.name,
                entity_type=custom.entity_type,
                llm_prompt=custom.llm_prompt,
                context_words=list(custom.context_words or []),
                rule_id=custom.id,
                base_url=base_url,
                model=model,
            )
        )

    return _build


class RecognizerRegistry(_CoreRecognizerRegistry):
    """Backend registry that pre-wires the Ollama-backed LLM factory."""

    def __init__(self, settings: Any | None = None) -> None:
        """Wire the LLM factory to the Ollama URL and model of ``settings`` (an ``AppSettings`` row)."""
        super().__init__(llm_recognizer_factory=_llm_recognizer_factory(settings))


__all__ = [
    "LLMContextConfig",
    "LLMContextRecognizer",
    "RecognizerRegistry",
]
