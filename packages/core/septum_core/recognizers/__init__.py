"""Recognizer registry and built-in regulation packs for septum-core.

Loads :class:`RecognizerRegistry` and the shared ``base_recognizer``
utilities. Regulation packs live under ``septum_core.recognizers.<id>``
and each exposes a ``get_recognizers()`` entry point.

:class:`RegulationId` (the StrEnum) and :data:`BUILTIN_REGULATION_IDS`
are the canonical source of truth for the 17 built-in packs, shared
by ``septum-core``, ``septum-mcp`` and the ``septum-api`` seed. Every
downstream consumer imports from here so the list cannot drift.

Because :class:`RegulationId` is a :class:`~enum.StrEnum`, its members
compare equal to plain strings (``RegulationId.GDPR == "gdpr"``), so
env-var-driven config, DB columns and REST payloads continue to flow
as strings without any adapter layer.
"""

from __future__ import annotations

import importlib
from enum import StrEnum
from typing import List

from .base_recognizer import RegexPatternConfig, ValidatedPatternRecognizer
from .registry import RecognizerRegistry


class RegulationId(StrEnum):
    """Canonical IDs of the built-in regulation packs shipped with septum-core."""

    GDPR = "gdpr"
    KVKK = "kvkk"
    CCPA = "ccpa"
    CPRA = "cpra"
    HIPAA = "hipaa"
    LGPD = "lgpd"
    PIPEDA = "pipeda"
    PDPA_TH = "pdpa_th"
    PDPA_SG = "pdpa_sg"
    APPI = "appi"
    PIPL = "pipl"
    POPIA = "popia"
    DPDP = "dpdp"
    UK_GDPR = "uk_gdpr"
    PDPL_SA = "pdpl_sa"
    NZPA = "nzpa"
    AUSTRALIA_PA = "australia_pa"


BUILTIN_REGULATION_IDS: tuple[RegulationId, ...] = tuple(RegulationId)


def parse_active_regulations_env(env_value: str | None) -> list[str]:
    """Parse the ``DEFAULT_ACTIVE_REGULATIONS``-style CSV env value.

    Empty / missing / whitespace-only inputs fall back to all built-in
    packs. Entries are lower-cased and stripped; empty splits are dropped.
    Unknown ids are *not* filtered here — downstream callers (e.g. the
    policy composer) already degrade gracefully on unknown ids and the
    validation surface we care about for user typos is the REST layer,
    not the operator-controlled env var.
    """
    if not env_value or not env_value.strip():
        return list(BUILTIN_REGULATION_IDS)
    parsed = [r.strip().lower() for r in env_value.split(",") if r.strip()]
    return parsed or list(BUILTIN_REGULATION_IDS)


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
    "RegulationId",
    "entity_types_for",
    "parse_active_regulations_env",
    "RecognizerRegistry",
    "RegexPatternConfig",
    "ValidatedPatternRecognizer",
]
