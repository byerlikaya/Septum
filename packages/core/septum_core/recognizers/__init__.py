"""Recognizer registry and built-in regulation packs for septum-core.

Loads :class:`RecognizerRegistry` and the shared ``base_recognizer``
utilities. Regulation packs live under ``septum_core.recognizers.<id>``
and each exposes a ``get_recognizers()`` entry point.

``BUILTIN_REGULATION_IDS`` and ``entity_types_for()`` are the canonical
source of truth shared by ``septum-core``, ``septum-mcp`` and the
``septum-api`` seed/defaults. Every downstream consumer imports from
here so the 17-pack list cannot drift.
"""

from __future__ import annotations

import importlib
from typing import List

from .base_recognizer import RegexPatternConfig, ValidatedPatternRecognizer
from .registry import RecognizerRegistry

BUILTIN_REGULATION_IDS: tuple[str, ...] = (
    "gdpr",
    "kvkk",
    "ccpa",
    "cpra",
    "hipaa",
    "lgpd",
    "pipeda",
    "pdpa_th",
    "pdpa_sg",
    "appi",
    "pipl",
    "popia",
    "dpdp",
    "uk_gdpr",
    "pdpl_sa",
    "nzpa",
    "australia_pa",
)


def entity_types_for(reg_id: str) -> List[str]:
    """Return the union of entity types declared by a regulation pack.

    Prefers the pack's ``ENTITY_TYPES`` constant (authoritative — includes
    NER/semantic types like ``PERSON_NAME`` that no Presidio recognizer
    declares). Falls back to walking each recognizer's
    ``supported_entities`` so **third-party packs** that ship without
    ``ENTITY_TYPES`` still work. Unknown regulation ids return an empty
    list; the policy composer will still produce a valid
    :class:`~septum_core.regulations.composer.ComposedPolicy` without
    that pack's recognizers.
    """
    pkg_path = f"septum_core.recognizers.{reg_id}"
    try:
        pkg = importlib.import_module(pkg_path)
    except ModuleNotFoundError:
        return []
    declared = getattr(pkg, "ENTITY_TYPES", None)
    if declared is not None:
        return list(declared)

    try:
        module = importlib.import_module(f"{pkg_path}.recognizers")
    except ModuleNotFoundError:
        return []
    get_recognizers = getattr(module, "get_recognizers", None)
    if get_recognizers is None:
        return []
    try:
        recognizers = list(get_recognizers())
    except Exception:
        return []
    seen: set[str] = set()
    result: List[str] = []
    for recognizer in recognizers:
        for et in getattr(recognizer, "supported_entities", ()):
            if et not in seen:
                seen.add(et)
                result.append(et)
    return result


__all__ = [
    "BUILTIN_REGULATION_IDS",
    "entity_types_for",
    "RecognizerRegistry",
    "RegexPatternConfig",
    "ValidatedPatternRecognizer",
]
