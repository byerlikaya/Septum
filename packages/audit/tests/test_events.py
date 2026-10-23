"""AuditRecord serialisation, strict ledger parsing, and the depth and non-finite rules applied to values."""

from __future__ import annotations

import json
import math

import pytest

from septum_audit import AuditRecord
from septum_audit.events import (
    GENESIS_PREV_HASH,
    DEPTH_LIMIT,
    DEPTH_LIMIT_MARKER,
    dumps_safe,
    json_safe,
    known_age_reference,
    loads_strict,
    normalise_attributes,
    parse_line,
    parse_record,
)

from _helpers import NOW, deep


def test_record_assigns_default_id_and_timestamp():
    rec = AuditRecord(source="septum-api", event_type="pii.detected")
    assert rec.id
    assert len(rec.id) == 32
    assert rec.timestamp > 0
    assert rec.attributes == {}
    assert rec.correlation_id is None


def test_record_round_trip_via_json_preserves_fields():
    rec = AuditRecord(
        source="septum-gateway",
        event_type="llm.request.completed",
        correlation_id="abc",
        attributes={"latency_ms": 312.5, "provider": "openai"},
    )
    rebuilt = AuditRecord.from_dict(json.loads(rec.to_json()))
    assert rebuilt.id == rec.id
    assert rebuilt.timestamp == rec.timestamp
    assert rebuilt.source == rec.source
    assert rebuilt.event_type == rec.event_type
    assert rebuilt.correlation_id == "abc"
    assert rebuilt.attributes == {"latency_ms": 312.5, "provider": "openai"}


def test_from_dict_falls_back_to_defaults_when_fields_missing():
    rebuilt = AuditRecord.from_dict({})
    assert rebuilt.source == "unknown"
    assert rebuilt.event_type == "unknown"
    assert rebuilt.attributes == {}
    assert rebuilt.correlation_id is None


@pytest.mark.parametrize(
    "raw",
    ["1700000000.5", "0", True, False, None, 0, 0.0, -0.0, float("inf"), float("-inf"), float("nan"), 10**400, [1], {}],
    ids=repr,
)
def test_from_dict_turns_anything_but_a_finite_non_zero_number_into_nan_for_the_sink_to_replace(raw):
    stored = AuditRecord.from_dict({"timestamp": raw}).timestamp
    assert math.isnan(stored)


@pytest.mark.parametrize("raw", [1, 1700000000, 1700000000.5, -5.0, 1e-300])
def test_from_dict_keeps_a_finite_non_zero_number(raw):
    assert AuditRecord.from_dict({"timestamp": raw}).timestamp == float(raw)


def test_a_mapping_with_string_keys_is_unchanged_and_not_flagged():
    attributes, changed = normalise_attributes({"k": "v", "n": 1})
    assert attributes == {"k": "v", "n": 1}
    assert changed is False


def test_a_mapping_with_non_string_keys_gets_string_keys_and_is_flagged():
    attributes, changed = normalise_attributes({2: "b", 1: "a"})
    assert attributes == {"2": "b", "1": "a"}
    assert changed is True


def test_a_mapping_holding_a_non_finite_number_is_flagged_and_the_number_becomes_a_string():
    attributes, changed = normalise_attributes({"v": float("inf")})
    assert attributes == {"v": "Infinity"}
    assert changed is True


def test_a_list_of_pairs_becomes_a_dict_in_pair_order():
    attributes, changed = normalise_attributes([["b", 1], ["a", 2]])
    assert list(attributes.items()) == [("b", 1), ("a", 2)]
    assert changed is True


def test_a_tuple_of_pairs_becomes_a_dict_and_an_empty_list_becomes_an_empty_dict():
    assert normalise_attributes((("a", 1),)) == ({"a": 1}, True)
    assert normalise_attributes([]) == ({}, True)


def test_numeric_pair_keys_become_strings_in_order_of_the_pairs():
    attributes, changed = normalise_attributes([[10, "a"], [9, "b"]])
    assert list(attributes.items()) == [("10", "a"), ("9", "b")]
    assert changed is True


@pytest.mark.parametrize("scalar", [5, "text", None, True, 1.5])
def test_a_scalar_is_wrapped_under_value(scalar):
    assert normalise_attributes(scalar) == ({"value": scalar}, True)


def test_a_list_that_is_not_all_pairs_is_wrapped_whole_under_value():
    assert normalise_attributes([["a", 1], ["b", 2, 3]]) == ({"value": [["a", 1], ["b", 2, 3]]}, True)
    assert normalise_attributes([1, 2]) == ({"value": [1, 2]}, True)


def test_key_collision_keeps_every_pair_under_value():
    attributes, changed = normalise_attributes([[1, "a"], ["1", "b"]])
    assert attributes == {"value": [["1", "a"], ["1", "b"]]}
    assert changed is True


def test_key_collision_in_a_mapping_keeps_every_pair_under_value():
    attributes, changed = normalise_attributes({1: "a", "1": "b"})
    assert attributes == {"value": [["1", "a"], ["1", "b"]]}
    assert changed is True


def test_a_value_json_cannot_serialise_falls_back_to_its_string_form():
    class Opaque:
        def __repr__(self) -> str:
            return "opaque"

        __str__ = __repr__

    attributes, changed = normalise_attributes({"k": Opaque()})
    assert attributes == {"value": "<unserialisable>"}
    assert changed is True


def test_the_normalised_attributes_are_what_json_reads_back():
    attributes, _ = normalise_attributes({"x": {"y": [1, (2, 3)]}})
    assert json.loads(json.dumps(attributes)) == attributes


def test_known_age_reference_prefers_received_at_over_timestamp():
    assert known_age_reference({"received_at": 5.0, "timestamp": 9.0}) == 5.0
    assert known_age_reference({"received_at": 0, "timestamp": 9.0}) == 0.0


def test_known_age_reference_falls_back_to_a_finite_non_zero_timestamp():
    assert known_age_reference({"timestamp": 9}) == 9.0
    assert known_age_reference({"timestamp": "12.5"}) == 12.5


@pytest.mark.parametrize("timestamp", [None, 0, 0.0, "0", float("inf"), float("-inf"), float("nan")], ids=repr)
def test_known_age_reference_is_unknown_for_a_missing_zero_or_non_finite_timestamp(timestamp):
    assert known_age_reference({"timestamp": timestamp}) is None
    assert known_age_reference({}) is None


@pytest.mark.parametrize("payload", [{"timestamp": 10**400}, {"received_at": 10**400, "timestamp": 1.0}])
def test_known_age_reference_turns_an_out_of_range_int_into_a_value_error(payload):
    with pytest.raises(ValueError, match="not a number in range"):
        known_age_reference(payload)


def test_expected_hash_is_the_record_hash_and_covers_the_attributes():
    record = AuditRecord(id="a", timestamp=NOW, source="s", event_type="e").with_hash_chain(prev_hash=GENESIS_PREV_HASH)
    assert record.expected_hash() == record.hash
    edited = AuditRecord(
        id="a", timestamp=NOW, source="s", event_type="e", attributes={"v": 1}
    ).with_hash_chain(prev_hash=GENESIS_PREV_HASH)
    assert edited.expected_hash() != record.hash


def test_a_record_without_received_at_serialises_without_the_key():
    record = AuditRecord(id="x", source="s", event_type="e")
    assert "received_at" not in record.to_dict()
    assert "received_at" not in json.loads(record.to_json())


def test_a_zero_received_at_is_preserved():
    record = AuditRecord.from_dict({**AuditRecord(id="x", source="s", event_type="e").to_dict(), "received_at": 0.0})
    assert record.received_at == 0.0
    assert record.to_dict()["received_at"] == 0.0


def test_non_finite_timestamp_cannot_be_serialised():
    with pytest.raises(ValueError):
        AuditRecord(id="x", timestamp=float("nan"), source="s", event_type="e").to_json()


def test_to_dict_is_shallow_and_survives_extreme_attribute_depth():
    nested = deep(5000)
    record = AuditRecord(id="d", attributes={"deep": nested})
    assert record.to_dict()["attributes"]["deep"] is nested


def test_duplicate_keys_are_refused():
    with pytest.raises(ValueError, match="duplicate key"):
        loads_strict('{"id":"a","id":"b"}')


@pytest.mark.parametrize("text", ["[]", "null", "1", '"x"'])
def test_loads_strict_rejects_a_non_object(text: str):
    with pytest.raises(ValueError, match="not a JSON object"):
        loads_strict(text)


def test_parse_line_and_parse_record_turn_recursion_into_value_error():
    with pytest.raises(ValueError):
        parse_line("[" * 300_000)
    with pytest.raises(ValueError):
        parse_record("[" * 300_000)


def test_parse_record_maps_a_wrong_type_to_value_error():
    with pytest.raises(ValueError):
        parse_record('{"id":"x","received_at":[1]}')


def test_parse_record_turns_a_recursion_error_from_record_building_into_a_value_error(monkeypatch: pytest.MonkeyPatch):
    def _boom(cls, data):
        raise RecursionError

    monkeypatch.setattr(AuditRecord, "from_dict", classmethod(_boom))
    with pytest.raises(ValueError):
        parse_record('{"id":"x"}')


def test_json_safe_converts_tuples_and_reaches_non_finite_values_inside_them():
    safe, changed = json_safe({"a": (1, (float("nan"), float("-inf")))})
    assert safe == {"a": [1, ["NaN", "-Infinity"]]}
    assert changed is True
    assert json_safe((float("inf"),)) == (["Infinity"], True)
    assert dumps_safe({"t": (float("nan"),)}) == '{"t":["NaN"]}'


def test_json_safe_depth_cap_replaces_deeper_containers_and_reports_the_change():
    value: object = "leaf"
    for _ in range(DEPTH_LIMIT + 5):
        value = [value]
    safe, changed = json_safe(value)
    assert changed is True
    depth = 0
    node = safe
    while isinstance(node, list):
        node = node[0]
        depth += 1
    assert node == DEPTH_LIMIT_MARKER
    assert depth == DEPTH_LIMIT + 1
    assert json_safe([[1]]) == ([[1]], False)


@pytest.mark.parametrize(
    "payload",
    [{"timestamp": [1]}, {"timestamp": {"a": 1}}, {"received_at": [1.0], "timestamp": 1.0}],
    ids=["list timestamp", "dict timestamp", "list received_at"],
)
def test_known_age_reference_turns_a_non_numeric_type_into_a_value_error(payload):
    with pytest.raises(ValueError, match="not a number in range"):
        known_age_reference(payload)
