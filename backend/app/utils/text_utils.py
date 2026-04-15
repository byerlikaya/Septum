"""Backward-compatibility shim.

The implementation of these helpers now lives in :mod:`septum_core.text_utils`.
This module re-exports the public API so existing ``from app.utils.text_utils``
imports continue to work.
"""

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
