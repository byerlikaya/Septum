from __future__ import annotations

"""Backward-compatibility shim over :mod:`septum_core.ner_model_registry`."""

from septum_core.ner_model_registry import (
    NERModelRegistry,
    get_shared_ner_registry,
)

__all__ = ["NERModelRegistry", "get_shared_ner_registry"]
