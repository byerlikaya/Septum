"""Ingest normalisation matrix: every producer shape is stored, reads back, verifies and survives a second round trip."""

from __future__ import annotations

from pathlib import Path

import pytest

from septum_audit import AuditRecord, JsonlFileSink
from septum_audit.events import INGEST_FLAG

NOW = 1_700_000_000.0

PAYLOADS = {
    "string zero": {"timestamp": "0"},
    "string minus zero": {"timestamp": "-0"},
    "string zero point zero": {"timestamp": "0.0"},
    "string tiny": {"timestamp": "1e-400"},
    "float zero": {"timestamp": 0.0},
    "negative zero": {"timestamp": -0.0},
    "bool true": {"timestamp": True},
    "bool false": {"timestamp": False},
    "huge int": {"timestamp": 10**400},
    "infinite": {"timestamp": float("inf")},
    "list of pairs": {"timestamp": NOW, "attributes": [["a", 1]]},
    "numeric pairs out of order": {"timestamp": NOW, "attributes": [[10, "a"], [9, "b"]]},
    "mixed key types": {"timestamp": NOW, "attributes": [["b", 1], [1, "a"]]},
    "scalar attributes": {"timestamp": NOW, "attributes": 5},
    "nested attributes": {"timestamp": NOW, "attributes": {"x": {"y": [1, (2, 3)]}}},
    "nested numeric keys": {"timestamp": NOW, "attributes": {"x": {2: "b", 1: "a"}}},
    "nan attribute": {"timestamp": NOW, "attributes": {"v": float("nan")}},
    "normal": {"timestamp": NOW, "attributes": {"k": "v"}},
}


@pytest.mark.parametrize("name", list(PAYLOADS))
async def test_every_producer_shape_round_trips_and_verifies(tmp_path: Path, name: str):
    target = tmp_path / "audit.jsonl"
    record = AuditRecord.from_dict({"id": "x", "source": "s", "event_type": "e", **PAYLOADS[name]})
    sink = JsonlFileSink(target)

    await sink.write(record)
    first = list(sink.read_all())
    assert len(first) == 1
    sink.verify()

    await sink.write(AuditRecord.from_dict(first[0].to_dict()))
    second = list(sink.read_all())
    assert len(second) == 2
    sink.verify()
    assert second[1].timestamp == first[0].timestamp
    assert _without_flag(second[1].attributes) == _without_flag(first[0].attributes)


def _without_flag(attributes: dict) -> dict:
    return {key: value for key, value in attributes.items() if key != INGEST_FLAG}
