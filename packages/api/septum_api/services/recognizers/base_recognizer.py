from __future__ import annotations

"""Backward-compatibility shim over :mod:`septum_core.recognizers.base_recognizer`."""

from septum_core.recognizers.base_recognizer import (
    RegexPatternConfig,
    ValidatedPatternRecognizer,
)

__all__ = ["RegexPatternConfig", "ValidatedPatternRecognizer"]
