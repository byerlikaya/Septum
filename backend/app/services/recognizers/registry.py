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
from typing import Iterable, List, Sequence

from presidio_analyzer import EntityRecognizer, Pattern, PatternRecognizer, RecognizerResult

from ...models.regulation import CustomRecognizer as CustomRecognizerModel
from ...models.regulation import RegulationRuleset

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
    Placeholder recognizer which will eventually delegate detection to a local
    LLM (for example via Ollama).

    For now this class only logs invocations and returns no detections so that
    the rest of the pipeline can be wired and tested without requiring a
    running LLM backend. Once the LLM router is implemented, the `analyze`
    method can be extended accordingly.
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
        if entities and not set(entities).intersection(self.supported_entities):
            return []

        logger.debug(
            "LLMContextRecognizer '%s' invoked for entity '%s'. "
            "LLM integration is not yet implemented; returning no matches.",
            self._config.name,
            self._config.entity_type,
        )
        return []


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
        recognizers: List[EntityRecognizer] = []
        # Derive the base package from this module's name so it works whether
        # the application is imported as `app.*` or `backend.app.*`.
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
        keywords: List[str] = list(custom.keywords or [])
        # Build a simple alternation pattern with word boundaries. Keywords are
        # escaped to avoid unintended regex behavior.
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
        config = LLMContextConfig(
            name=custom.name,
            entity_type=custom.entity_type,
            llm_prompt=custom.llm_prompt or "",
            context_words=list(custom.context_words or []),
        )
        return LLMContextRecognizer(config)

