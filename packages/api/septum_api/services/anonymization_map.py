"""Re-export of :class:`septum_core.anonymization_map.AnonymizationMap` for api-side imports."""

from __future__ import annotations

from septum_core.anonymization_map import (  # noqa: F401
    PLACEHOLDER_PATTERN,
    PLACEHOLDER_SHAPE,
    SANITIZER_STOPWORDS,
    AnonymizationMap,
    restoration_lookup,
)

__all__ = [
    "AnonymizationMap",
    "PLACEHOLDER_PATTERN",
    "PLACEHOLDER_SHAPE",
    "SANITIZER_STOPWORDS",
    "restoration_lookup",
]
