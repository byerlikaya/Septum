"""The legal-source document, the pack ``ENTITY_TYPES`` and the pack recognizers agree.

``REGULATION_ENTITY_SOURCES.md`` is the documented legal basis for every
built-in pack. Each section's ``Entity-type mapping`` line must list exactly
the types its pack declares, and every recognizer a pack ships must emit only
types the pack declares (directly, or through a Presidio alias or a
parent-coverage type the detector expands to).
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest
from septum_core.detector import Detector
from septum_core.recognizers import BUILTIN_REGULATION_IDS, entity_types_for

_DOC = Path(__file__).resolve().parents[1] / "docs" / "REGULATION_ENTITY_SOURCES.md"
_MAPPING_PREFIX = "- **Entity-type mapping:**"
_TYPE_RE = re.compile(r"`([A-Z][A-Z0-9_]*)`")


def _documented_types() -> dict[str, list[str]]:
    sections = re.split(r"(?m)^## ", _DOC.read_text(encoding="utf-8"))[1:]
    documented: dict[str, list[str]] = {}
    for section in sections:
        lines = section.splitlines()
        mapping = [line for line in lines if line.startswith(_MAPPING_PREFIX)]
        if not mapping:
            continue
        reg_id = lines[0].split(" – ")[0].strip().lower()
        assert len(mapping) == 1, f"{reg_id}: {len(mapping)} entity-type mapping lines"
        documented[reg_id] = _TYPE_RE.findall(mapping[0])
    return documented


_DOCUMENTED = _documented_types()


def test_document_covers_exactly_the_builtin_regulations() -> None:
    assert sorted(_DOCUMENTED) == sorted(str(r) for r in BUILTIN_REGULATION_IDS)


@pytest.mark.parametrize("reg_id", [str(r) for r in BUILTIN_REGULATION_IDS])
def test_document_mapping_matches_pack_entity_types(reg_id: str) -> None:
    documented = set(_DOCUMENTED.get(reg_id, ()))
    declared = set(entity_types_for(reg_id))

    assert documented == declared, (
        f"{reg_id}: doc-only {sorted(documented - declared)}, "
        f"pack-only {sorted(declared - documented)}"
    )


@pytest.mark.parametrize("reg_id", [str(r) for r in BUILTIN_REGULATION_IDS])
def test_document_mapping_has_no_duplicates(reg_id: str) -> None:
    documented = _DOCUMENTED.get(reg_id, [])

    assert len(documented) == len(set(documented)), sorted(
        t for t in set(documented) if documented.count(t) > 1
    )


@pytest.mark.parametrize("reg_id", [str(r) for r in BUILTIN_REGULATION_IDS])
def test_pack_entity_types_have_no_duplicates(reg_id: str) -> None:
    declared = entity_types_for(reg_id)

    assert len(declared) == len(set(declared)), sorted(
        t for t in set(declared) if declared.count(t) > 1
    )


@pytest.mark.parametrize("reg_id", [str(r) for r in BUILTIN_REGULATION_IDS])
def test_pack_recognizers_emit_only_declared_types(reg_id: str) -> None:
    module = importlib.import_module(f"septum_core.recognizers.{reg_id}.recognizers")
    allowed = set(Detector._expand_with_aliases(entity_types_for(reg_id)) or ())
    emitted = {
        entity
        for recognizer in module.get_recognizers()
        for entity in recognizer.supported_entities
    }

    assert emitted <= allowed, f"{reg_id}: undeclared {sorted(emitted - allowed)}"
