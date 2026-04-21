from __future__ import annotations

"""
Shared utilities for Presidio recognizers used by Septum.

The classes in this module provide small, well-typed wrappers around Presidio's
`PatternRecognizer` so that regulation-specific packs can declare recognizers
in a concise and consistent way.
"""

import logging
import re
from dataclasses import dataclass
from typing import Callable, Final, List, Optional

from presidio_analyzer import Pattern, PatternRecognizer, RecognizerResult
from presidio_analyzer.nlp_engine import NlpArtifacts

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RegexPatternConfig:
    """Configuration for a single regex pattern used by a recognizer.

    ``narrow_to_group`` lets a pattern match a wider surrounding expression
    (for example "No: 12345678901") while reporting only a specific capture
    group as the detected entity span (the digits themselves). This keeps
    the span tight without sacrificing context-aware matching.

    ``fallback_score`` controls what happens when an algorithmic
    validator rejects a match. When it is ``None`` (the default) the
    match is dropped entirely — appropriate when a wide regex would
    otherwise leak unrelated digit sequences. When it is set to a
    positive float, the match is still emitted but at that reduced
    score, mirroring the over-detection philosophy already applied
    to ``CreditCardNumberRecognizer`` (no Luhn check) and the
    format-only IBAN fallback. This is how synthetic or typo'd
    national IDs stay masked in privacy-first pipelines.
    """

    name: str
    pattern: str
    score: float = 0.6
    narrow_to_group: Optional[int] = None
    fallback_score: Optional[float] = None


class ValidatedPatternRecognizer(PatternRecognizer):
    """Presidio pattern recognizer with optional algorithmic validation.

    When an ``algorithmic_validator`` is supplied, each regex match is
    routed through Presidio's ``validate_result`` hook so that
    checksum-bearing identifiers are verified at detection time.
    Invalid matches are either filtered out entirely or kept at a
    lower ``fallback_score`` depending on the ``RegexPatternConfig``.

    When ``narrow_to_group`` is set, the ``analyze`` override rewrites
    each ``RecognizerResult`` so the span reflects only the given capture
    group — useful when the enclosing regex also needs to match a
    preceding context keyword that should not be part of the entity.
    """

    def __init__(
        self,
        *,
        entity_type: str,
        config: RegexPatternConfig,
        supported_language: str = "en",
        algorithmic_validator: Optional[Callable[[str], bool]] = None,
    ) -> None:
        if not config.pattern:
            raise ValueError("Regex pattern must not be empty.")

        try:
            compiled: Final[re.Pattern[str]] = re.compile(config.pattern)
        except re.error as exc:  # pragma: no cover - defensive logging
            logger.error("Invalid regex pattern for recognizer %s: %s", config.name, exc)
            raise

        if config.narrow_to_group is not None and config.narrow_to_group > compiled.groups:
            raise ValueError(
                f"narrow_to_group={config.narrow_to_group} exceeds the number of "
                f"capture groups ({compiled.groups}) in pattern for "
                f"recognizer {config.name!r}."
            )

        pattern = Pattern(
            name=config.name,
            regex=compiled.pattern,
            score=config.score,
        )

        super().__init__(
            supported_entity=entity_type,
            patterns=[pattern],
            name=config.name,
            supported_language=supported_language,
        )

        self._narrow_to_group = config.narrow_to_group
        self._fallback_score = config.fallback_score
        self._algorithmic_validator = algorithmic_validator
        self._compiled_pattern = compiled

    def validate_result(self, pattern_text: str) -> Optional[bool]:
        """Run the algorithmic validator if one was configured.

        Returning ``True`` makes Presidio promote the score to 1.0;
        returning ``False`` drives it to 0.0 which the sanitizer treats
        as a non-detection (unless a ``fallback_score`` is set — see
        the ``analyze`` override below). Returning ``None`` (when no
        validator is attached) leaves the base pattern score untouched.
        """
        if self._algorithmic_validator is None:
            return None
        try:
            return bool(self._algorithmic_validator(pattern_text))
        except Exception:  # noqa: BLE001 - validators must never crash analysis
            logger.warning(
                "Algorithmic validator raised on %r for recognizer %s",
                pattern_text,
                self.name,
                exc_info=True,
            )
            return False

    def analyze(
        self,
        text: str,
        entities: List[str],
        nlp_artifacts: Optional[NlpArtifacts] = None,
    ) -> List[RecognizerResult]:
        """Extend Presidio's ``analyze`` with capture-group narrowing.

        The stock Presidio path handles recognizers that do not need
        ``narrow_to_group`` and that rely on the default drop-on-false
        behaviour. When either a capture-group narrow or a
        ``fallback_score`` is configured we fully replicate Presidio's
        scoring logic locally because there is no public hook that
        lets us both report a capture-group offset and keep a match
        alive after a failing validator.
        """
        if self._narrow_to_group is None and self._fallback_score is None:
            return super().analyze(text, entities, nlp_artifacts)

        if entities and self.supported_entities[0] not in entities:
            return []

        results: List[RecognizerResult] = []
        base_score = self.patterns[0].score
        group_index = self._narrow_to_group

        for match in self._compiled_pattern.finditer(text):
            if group_index is not None:
                span_start = match.start(group_index)
                span_end = match.end(group_index)
                if span_start < 0 or span_end < 0:
                    continue
                candidate_text = match.group(group_index)
            else:
                span_start = match.start()
                span_end = match.end()
                candidate_text = match.group(0)

            validation = self.validate_result(candidate_text)
            if validation is False:
                if self._fallback_score is None:
                    continue
                score = self._fallback_score
            elif validation is True:
                score = 1.0
            else:
                score = base_score

            results.append(
                RecognizerResult(
                    entity_type=self.supported_entities[0],
                    start=span_start,
                    end=span_end,
                    score=score,
                    analysis_explanation=None,
                    recognition_metadata={
                        RecognizerResult.RECOGNIZER_NAME_KEY: self.name,
                        RecognizerResult.RECOGNIZER_IDENTIFIER_KEY: self.id,
                    },
                )
            )

        return results
