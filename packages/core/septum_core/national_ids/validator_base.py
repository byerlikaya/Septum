from __future__ import annotations

import abc
import re
from dataclasses import dataclass
from typing import Final, Pattern


@dataclass(frozen=True)
class ValidationResult:
    """Represents the outcome of validating a national identifier value."""

    is_valid: bool
    normalized_value: str


class BaseIDValidator(abc.ABC):
    """Abstract base class for country-specific national ID validators."""

    name: Final[str]
    pattern: Final[Pattern[str]]

    def __init__(self) -> None:
        if not hasattr(self, "name"):
            raise ValueError("Validator must define a 'name' attribute.")
        if not hasattr(self, "pattern"):
            raise ValueError("Validator must define a compiled 'pattern' attribute.")

    def validate(self, value: str) -> bool:
        """Return True if the given value is a syntactically and algorithmically valid ID."""
        normalized = self._normalize(value)
        if not self.pattern.fullmatch(normalized):
            return False
        return self._validate_algorithmically(normalized)

    def validate_detailed(self, value: str) -> ValidationResult:
        """Return a detailed validation result including the normalized value."""
        normalized = self._normalize(value)
        if not self.pattern.fullmatch(normalized):
            return ValidationResult(is_valid=False, normalized_value=normalized)
        is_valid = self._validate_algorithmically(normalized)
        return ValidationResult(is_valid=is_valid, normalized_value=normalized)

    @staticmethod
    def _normalize(value: str) -> str:
        """Normalize the raw value by stripping whitespace and common separators."""
        return re.sub(r"[\s.-]", "", value)

    @abc.abstractmethod
    def _validate_algorithmically(self, normalized: str) -> bool:
        """Perform checksum or rule-based algorithmic validation on a normalized value."""
