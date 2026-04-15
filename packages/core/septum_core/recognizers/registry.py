from __future__ import annotations

"""
Recognizer registry for septum-core.

Builds Presidio recognizers from:
- Built-in regulation packs (for example GDPR, HIPAA, KVKK).
- User-defined :class:`CustomRecognizerLike` records passed in by the
  caller.

The core registry is intentionally network-free: custom recognizers
that use ``detection_method='llm_prompt'`` are built through an
optional ``llm_recognizer_factory`` callback injected by the caller.
If no factory is supplied, LLM-backed custom recognizers are skipped
with a warning. This keeps all network/LLM plumbing outside the
air-gapped core.
"""

import importlib
import logging
import re
from typing import Callable, List, Optional, Sequence

from presidio_analyzer import (
    EntityRecognizer,
    Pattern,
    PatternRecognizer,
)

from ..regulations.models import CustomRecognizerLike, RegulationRulesetLike

logger = logging.getLogger(__name__)

LLMRecognizerFactory = Callable[[CustomRecognizerLike], Optional[EntityRecognizer]]


class RecognizerRegistry:
    """
    Build Presidio recognizers for the active regulations and custom rules.

    The registry is deliberately stateless; callers pass in the active
    regulation rulesets and custom recognizers, and the registry returns
    a flat list of :class:`EntityRecognizer` instances.

    An optional ``llm_recognizer_factory`` lets hosts (backend,
    MCP server, tests) inject a concrete LLM-backed recognizer
    implementation for custom rules whose ``detection_method`` is
    ``'llm_prompt'``. When omitted, such rules are skipped so the
    core package never attempts an outbound network call on its own.
    """

    def __init__(
        self,
        llm_recognizer_factory: Optional[LLMRecognizerFactory] = None,
    ) -> None:
        self._llm_recognizer_factory = llm_recognizer_factory

    def build(
        self,
        active_regs: Sequence[RegulationRulesetLike],
        custom_recognizers: Sequence[CustomRecognizerLike],
    ) -> List[EntityRecognizer]:
        """Return all recognizers for the given regulations and custom rules."""
        recognizers: List[EntityRecognizer] = []
        recognizers.extend(self._load_builtin_packs(active_regs))
        recognizers.extend(self._from_custom_recognizers(custom_recognizers))
        return recognizers

    def _load_builtin_packs(
        self,
        active_regs: Sequence[RegulationRulesetLike],
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
        custom_recognizers: Sequence[CustomRecognizerLike],
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
                llm_recognizer = self._build_llm_recognizer(custom)
                if llm_recognizer is not None:
                    recognizers.append(llm_recognizer)
            else:
                logger.warning(
                    "Skipping custom recognizer %s (%s) due to incomplete configuration.",
                    custom.id,
                    custom.name,
                )
        return recognizers

    @staticmethod
    def _build_regex_recognizer(
        custom: CustomRecognizerLike,
    ) -> Optional[PatternRecognizer]:
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
        custom: CustomRecognizerLike,
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

    def _build_llm_recognizer(
        self,
        custom: CustomRecognizerLike,
    ) -> Optional[EntityRecognizer]:
        """Delegate LLM-backed custom recognizer construction to the host factory.

        When no factory is injected (the default for air-gapped hosts),
        the custom rule is skipped and a warning is logged.
        """
        if self._llm_recognizer_factory is None:
            logger.warning(
                "Custom recognizer %s (%s) uses detection_method='llm_prompt' but "
                "no llm_recognizer_factory is attached to the registry; skipping.",
                custom.id,
                custom.name,
            )
            return None
        try:
            return self._llm_recognizer_factory(custom)
        except Exception:  # pragma: no cover - defensive logging
            logger.exception(
                "LLM recognizer factory failed for custom rule %s (%s); skipping.",
                custom.id,
                custom.name,
            )
            return None
