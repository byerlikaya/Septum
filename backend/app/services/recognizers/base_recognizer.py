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
    """

    name: str
    pattern: str
    score: float = 0.6
    narrow_to_group: Optional[int] = None


class ValidatedPatternRecognizer(PatternRecognizer):
    """Presidio pattern recognizer with optional algorithmic validation.

    When an ``algorithmic_validator`` is supplied, each regex match is
    routed through Presidio's ``validate_result`` hook so that
    checksum-bearing identifiers are verified at detection time. Invalid
    matches are filtered out instead of surviving at a reduced score.

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
        self._algorithmic_validator = algorithmic_validator
        self._compiled_pattern = compiled

    def validate_result(self, pattern_text: str) -> Optional[bool]:
        """Run the algorithmic validator if one was configured.

        Returning ``True`` makes Presidio promote the score to 1.0;
        returning ``False`` drives it to 0.0 which the sanitizer treats
        as a non-detection. Returning ``None`` (when no validator is
        attached) leaves the base pattern score untouched.
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
        """Extend Presidio's ``analyze`` with capture-group span narrowing.

        When ``narrow_to_group`` is configured we bypass Presidio's
        finditer-over-the-whole-match path because Presidio has no public
        hook for reporting capture-group offsets. We replicate its score
        + validation logic locally: score starts at the pattern base,
        gets pinned to 1.0 on a passing validator, dropped to 0.0 on a
        failing one. Recognizers without a narrow_to_group fall back to
        the stock Presidio behaviour (which already calls
        ``validate_result``).
        """
        if self._narrow_to_group is None:
            return super().analyze(text, entities, nlp_artifacts)

        if entities and self.supported_entities[0] not in entities:
            return []

        results: List[RecognizerResult] = []
        base_score = self.patterns[0].score
        group_index = self._narrow_to_group

        for match in self._compiled_pattern.finditer(text):
            group_start = match.start(group_index)
            group_end = match.end(group_index)
            if group_start < 0 or group_end < 0:
                continue
            group_text = match.group(group_index)

            validation = self.validate_result(group_text)
            if validation is False:
                continue
            score = 1.0 if validation is True else base_score

            results.append(
                RecognizerResult(
                    entity_type=self.supported_entities[0],
                    start=group_start,
                    end=group_end,
                    score=score,
                    analysis_explanation=None,
                    recognition_metadata={
                        RecognizerResult.RECOGNIZER_NAME_KEY: self.name,
                        RecognizerResult.RECOGNIZER_IDENTIFIER_KEY: self.id,
                    },
                )
            )

        return results
