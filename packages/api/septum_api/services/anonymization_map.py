"""Re-export of :class:`septum_core.anonymization_map.AnonymizationMap` for api-side imports."""

from __future__ import annotations

from septum_core.anonymization_map import (  # noqa: F401
    SANITIZER_STOPWORDS,
    AnonymizationMap,
)

__all__ = ["AnonymizationMap", "SANITIZER_STOPWORDS"]
