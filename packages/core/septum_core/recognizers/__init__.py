"""Recognizer registry and built-in regulation packs for septum-core.

Loads :class:`RecognizerRegistry` and the shared ``base_recognizer``
utilities. Regulation packs live under ``septum_core.recognizers.<id>``
and each exposes a ``get_recognizers()`` entry point.
"""

from .base_recognizer import RegexPatternConfig, ValidatedPatternRecognizer
from .registry import RecognizerRegistry

__all__ = [
    "RecognizerRegistry",
    "RegexPatternConfig",
    "ValidatedPatternRecognizer",
]
