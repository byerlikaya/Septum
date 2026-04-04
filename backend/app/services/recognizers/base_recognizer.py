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
from typing import Final

from presidio_analyzer import Pattern, PatternRecognizer

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RegexPatternConfig:
    """Configuration for a single regex pattern used by a recognizer."""

    name: str
    pattern: str
    score: float = 0.6


class ValidatedPatternRecognizer(PatternRecognizer):
    """
    Thin wrapper around Presidio's `PatternRecognizer` with basic regex
    validation and sensible defaults.

    This class intentionally keeps behavior simple: it validates that the
    provided pattern compiles and then forwards configuration to the base
    recognizer. More advanced behavior (for example country-specific checksum
    logic) lives in dedicated validator classes under `national_ids/`.
    """

    def __init__(
        self,
        *,
        entity_type: str,
        config: RegexPatternConfig,
        supported_language: str = "en",
    ) -> None:
        if not config.pattern:
            raise ValueError("Regex pattern must not be empty.")

        try:
            compiled: Final[re.Pattern[str]] = re.compile(config.pattern)
        except re.error as exc:  # pragma: no cover - defensive logging
            logger.error("Invalid regex pattern for recognizer %s: %s", config.name, exc)
            raise

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

