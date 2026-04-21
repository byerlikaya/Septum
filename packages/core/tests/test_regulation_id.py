"""Smoke tests for the ``RegulationId`` StrEnum invariants that downstream
callers (MCP, API seed, REST payloads) silently depend on.

Every test here protects an assumption that would break the refactor if
someone swapped the base class back to :class:`enum.Enum` or reordered
members.
"""

from __future__ import annotations

import pytest

from septum_core.recognizers import (
    BUILTIN_REGULATION_IDS,
    RegulationId,
    parse_active_regulations_env,
)


def test_regulation_id_equals_plain_string() -> None:
    assert RegulationId.GDPR == "gdpr"
    assert "gdpr" in {RegulationId.GDPR}


def test_regulation_id_formats_as_its_value() -> None:
    # StrEnum overrides __format__ to return the value, plain Enum does not.
    # f-string interpolation must yield the id, not "RegulationId.GDPR".
    assert f"{RegulationId.GDPR}" == "gdpr"


def test_regulation_id_unknown_value_raises() -> None:
    with pytest.raises(ValueError):
        RegulationId("nonexistent")


def test_builtin_regulation_ids_matches_enum_members() -> None:
    assert BUILTIN_REGULATION_IDS == tuple(RegulationId)


def test_parse_active_regulations_env_empty_returns_all_builtins() -> None:
    assert parse_active_regulations_env(None) == list(BUILTIN_REGULATION_IDS)
    assert parse_active_regulations_env("") == list(BUILTIN_REGULATION_IDS)
    assert parse_active_regulations_env("   ") == list(BUILTIN_REGULATION_IDS)


def test_parse_active_regulations_env_lowercases_and_strips() -> None:
    assert parse_active_regulations_env(" GDPR , KVKK ") == ["gdpr", "kvkk"]


def test_parse_active_regulations_env_drops_empty_splits() -> None:
    assert parse_active_regulations_env("gdpr,,kvkk,") == ["gdpr", "kvkk"]
