"""Backward-compatibility shim.

The implementation of :class:`AnonymizationMap` and ``SANITIZER_STOPWORDS``
now lives in :mod:`septum_core.anonymization_map`. This module re-exports the
public symbols so existing ``from app.services.anonymization_map`` imports
continue to work.
"""

from __future__ import annotations

from septum_core.anonymization_map import (  # noqa: F401
    SANITIZER_STOPWORDS,
    AnonymizationMap,
)

__all__ = ["AnonymizationMap", "SANITIZER_STOPWORDS"]
