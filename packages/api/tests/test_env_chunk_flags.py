"""Chunk ``*_DEFAULT`` env flags obey the same bounds as the settings PATCH."""

from __future__ import annotations

import pytest
from septum_api.env_flags import EnvFlagError, parse_int_default_flags
from septum_api.request_limits import (
    MAX_PRE_APPROVED_CHUNK_CHARS,
    MIN_CHUNK_SIZE,
    MIN_PDF_CHUNK_SIZE,
)


@pytest.mark.parametrize(
    "name,value",
    [
        ("CHUNK_SIZE_DEFAULT", MAX_PRE_APPROVED_CHUNK_CHARS + 1),
        ("CHUNK_SIZE_DEFAULT", MIN_CHUNK_SIZE - 1),
        ("PDF_CHUNK_SIZE_DEFAULT", MAX_PRE_APPROVED_CHUNK_CHARS + 1),
        ("PDF_CHUNK_SIZE_DEFAULT", MIN_PDF_CHUNK_SIZE - 1),
        ("AUDIO_CHUNK_SIZE_DEFAULT", MAX_PRE_APPROVED_CHUNK_CHARS + 1),
        ("SPREADSHEET_CHUNK_SIZE_DEFAULT", MAX_PRE_APPROVED_CHUNK_CHARS + 1),
        ("CHUNK_OVERLAP_DEFAULT", MAX_PRE_APPROVED_CHUNK_CHARS // 2 + 1),
    ],
)
def test_out_of_range_chunk_flag_refuses_naming_variable(
    monkeypatch: pytest.MonkeyPatch, name: str, value: int
) -> None:
    monkeypatch.setenv(name, str(value))

    with pytest.raises(EnvFlagError, match=name):
        parse_int_default_flags()


def test_overlap_above_half_the_chunk_size_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHUNK_SIZE_DEFAULT", "300")
    monkeypatch.delenv("CHUNK_OVERLAP_DEFAULT", raising=False)

    with pytest.raises(EnvFlagError, match="CHUNK_OVERLAP_DEFAULT") as excinfo:
        parse_int_default_flags()

    assert "300" not in str(excinfo.value)


def test_overlap_of_exactly_half_the_chunk_size_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHUNK_SIZE_DEFAULT", str(MAX_PRE_APPROVED_CHUNK_CHARS))
    monkeypatch.setenv("CHUNK_OVERLAP_DEFAULT", str(MAX_PRE_APPROVED_CHUNK_CHARS // 2))

    flags = parse_int_default_flags()

    assert flags["CHUNK_SIZE_DEFAULT"] == MAX_PRE_APPROVED_CHUNK_CHARS
    assert flags["CHUNK_OVERLAP_DEFAULT"] == MAX_PRE_APPROVED_CHUNK_CHARS // 2
