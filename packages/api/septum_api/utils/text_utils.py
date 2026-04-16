"""Re-export of :mod:`septum_core.text_utils` helpers for api-side imports."""

from __future__ import annotations

from septum_core.text_utils import (  # noqa: F401
    locale_lower,
    normalize_for_comparison,
    normalize_unicode,
    starts_with_uppercase,
    strip_control_characters,
    strip_possessive_suffix,
)

__all__ = [
    "locale_lower",
    "normalize_for_comparison",
    "normalize_unicode",
    "starts_with_uppercase",
    "strip_control_characters",
    "strip_possessive_suffix",
]
