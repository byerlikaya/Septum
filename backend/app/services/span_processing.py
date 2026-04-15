from __future__ import annotations

"""Backward-compatibility shim over :mod:`septum_core.span_processing`."""

from septum_core.span_processing import (
    deduplicate_spans,
    expand_person_name_spans,
    merge_adjacent_person_name_spans,
)

__all__ = [
    "deduplicate_spans",
    "expand_person_name_spans",
    "merge_adjacent_person_name_spans",
]
