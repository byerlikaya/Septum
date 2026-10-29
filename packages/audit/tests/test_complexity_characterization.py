"""Characterization of the branches behind the complexity refactor: the pure helpers of retention, json_safe, the worker task error and the export guard."""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

import pytest
from _helpers import DAY, NOW, chain, chained, deep, legacy_line, lines
from septum_audit import RetentionPolicy
from septum_audit.anchor import accepted_links, read_anchor
from septum_audit.events import DEPTH_LIMIT_MARKER, GENESIS_PREV_HASH, json_safe
from septum_audit.retention import _may_remove, _plan_prune, apply_retention_to_jsonl
from septum_audit.worker import _task_error


def _legacy_ages(ages: list[float | None]) -> bytes:
    """Hash-less lines whose age is ``ages[i]`` days before ``NOW``; ``None`` writes a timestamp with no usable age."""
    texts = [legacy_line(f"r-{index}", "0" if age is None else repr(NOW - age * DAY)) for index, age in enumerate(ages)]
    return ("\n".join(texts) + "\n").encode("utf-8")


def _plan(data: bytes, policy: RetentionPolicy):
    cutoff = policy.cutoff_timestamp(now=NOW)
    return _plan_prune(io.BytesIO(data), policy, cutoff, accepted_links(None), True)


def test_json_safe_passes_scalars_through_and_reports_only_non_finite_ones():
    assert json_safe(5) == (5, False)
    assert json_safe(1.5) == (1.5, False)
    assert json_safe("text") == ("text", False)
    assert json_safe(None) == (None, False)


@pytest.mark.parametrize(
    ("value", "text"),
    [(float("nan"), "NaN"), (float("inf"), "Infinity"), (float("-inf"), "-Infinity")],
)
def test_json_safe_scalar_non_finite_float_becomes_its_text(value: float, text: str):
    assert json_safe(value) == (text, True)


def test_json_safe_top_level_list_appends_replaced_items():
    assert json_safe([1, float("nan"), (2,)]) == ([1, "NaN", [2]], True)


def test_json_safe_turns_tuples_into_lists_and_leaves_the_source_untouched():
    source = {"a": (1, (2, float("nan"))), "b": [float("inf")]}

    copy, changed = json_safe(source)

    assert copy == {"a": [1, [2, "NaN"]], "b": ["Infinity"]}
    assert changed is True
    assert isinstance(source["a"], tuple)
    assert source["b"] == [float("inf")]


def test_json_safe_replaces_containers_deeper_than_the_cap():
    copy, changed = json_safe(deep(4), max_depth=2)

    assert copy == {"n": {"n": {"n": DEPTH_LIMIT_MARKER}}}
    assert changed is True


def test_json_safe_keeps_containers_at_the_cap_unchanged():
    copy, changed = json_safe(deep(3), max_depth=2)

    assert copy == deep(3)
    assert changed is False


def test_json_safe_without_a_depth_cap_keeps_deep_structures():
    copy, changed = json_safe(deep(40), max_depth=None)

    assert copy == deep(40)
    assert changed is False


async def test_task_error_names_returned_failed_and_cancelled_tasks():
    async def returns() -> None:
        return None

    async def fails() -> None:
        raise ValueError("boom")

    async def waits() -> None:
        await asyncio.Event().wait()

    done = asyncio.create_task(returns())
    failed = asyncio.create_task(fails())
    stopped = asyncio.create_task(waits())
    await asyncio.gather(done, failed, return_exceptions=True)
    stopped.cancel()
    await asyncio.gather(stopped, return_exceptions=True)

    assert _task_error(done) == "returned"
    assert _task_error(failed) == "ValueError"
    assert _task_error(stopped) == "cancelled"


def test_may_remove_is_false_for_an_empty_ledger():
    assert _may_remove(io.BytesIO(b""), RetentionPolicy(max_records=1), None) is False


def test_may_remove_is_true_for_a_torn_tail():
    data = lines(chain(1)).encode("utf-8") + b"partial"
    assert _may_remove(io.BytesIO(data), RetentionPolicy(max_records=5), None) is True


def test_may_remove_is_true_for_an_unparseable_first_line():
    data = b"not json\n" + lines(chain(1)).encode("utf-8")
    assert _may_remove(io.BytesIO(data), RetentionPolicy(max_age_days=2), NOW - 2 * DAY) is True


def test_may_remove_is_true_when_the_first_known_age_is_before_the_cutoff():
    data = lines(chain(2, timestamp=NOW - 10 * DAY)).encode("utf-8")
    assert _may_remove(io.BytesIO(data), RetentionPolicy(max_age_days=2), NOW - 2 * DAY) is True


def test_may_remove_is_false_when_the_first_known_age_is_within_the_cap():
    data = lines(chain(2, timestamp=NOW)).encode("utf-8")
    assert _may_remove(io.BytesIO(data), RetentionPolicy(max_age_days=2), NOW - 2 * DAY) is False


def test_may_remove_skips_unknown_ages_to_find_the_first_known_one():
    data = lines([chained("unknown", GENESIS_PREV_HASH, timestamp=0.0)]).encode("utf-8")
    data += lines(chain(1, timestamp=NOW - 10 * DAY)).encode("utf-8")
    assert _may_remove(io.BytesIO(data), RetentionPolicy(max_age_days=2), NOW - 2 * DAY) is True


def test_may_remove_count_cap_uses_the_slack_threshold():
    policy = RetentionPolicy(max_records=3)
    assert _may_remove(io.BytesIO(lines(chain(4)).encode("utf-8")), policy, None) is False
    assert _may_remove(io.BytesIO(lines(chain(5)).encode("utf-8")), policy, None) is True


def test_plan_prune_counts_the_leading_run_before_the_first_record_within_the_cap():
    data = _legacy_ages([10, 0.5])

    plan = _plan(data, RetentionPolicy(max_age_days=2))

    assert (plan.valid, plan.torn, plan.drop) == (2, 0, 1)


def test_plan_prune_unknown_age_takes_the_age_of_its_successor():
    data = _legacy_ages([10, None, 0.5])

    plan = _plan(data, RetentionPolicy(max_age_days=2))

    assert (plan.valid, plan.drop) == (3, 1)


def test_plan_prune_unknown_age_is_reset_by_a_later_known_age_and_everything_goes():
    data = _legacy_ages([10, None, 10])

    plan = _plan(data, RetentionPolicy(max_age_days=2))

    assert (plan.valid, plan.drop) == (3, 3)


def test_plan_prune_all_unknown_ages_are_kept():
    data = _legacy_ages([None, None])

    plan = _plan(data, RetentionPolicy(max_age_days=2))

    assert (plan.valid, plan.drop) == (2, 0)


def test_plan_prune_keeps_everything_when_the_first_record_is_within_the_cap():
    data = _legacy_ages([0.5, 10])

    plan = _plan(data, RetentionPolicy(max_age_days=2))

    assert (plan.valid, plan.drop) == (2, 0)


def test_plan_prune_reports_the_last_chained_hash_of_the_ledger():
    records = chain(2, timestamp=NOW - 10 * DAY)
    data = lines(records).encode("utf-8")

    plan = _plan(data, RetentionPolicy(max_age_days=2))

    assert plan.last_hash == records[-1].hash


def test_plan_prune_count_cap_trims_to_max_records_once_past_the_slack():
    data = lines(chain(5)).encode("utf-8")

    plan = _plan(data, RetentionPolicy(max_records=3))

    assert (plan.valid, plan.drop) == (5, 2)


def test_plan_prune_count_cap_is_idle_inside_the_slack():
    data = lines(chain(4)).encode("utf-8")

    plan = _plan(data, RetentionPolicy(max_records=3))

    assert (plan.valid, plan.drop) == (4, 0)


def test_plan_prune_takes_the_larger_of_the_age_and_count_drops():
    data = _legacy_ages([10, 0.5, 0.5, 0.5, 0.5])

    plan = _plan(data, RetentionPolicy(max_age_days=2, max_records=3))

    assert (plan.valid, plan.drop) == (5, 2)


def test_plan_prune_counts_a_torn_tail_without_counting_it_as_a_record():
    data = lines(chain(1)).encode("utf-8") + b"tail"

    plan = _plan(data, RetentionPolicy(max_records=5))

    assert (plan.valid, plan.torn, plan.drop) == (1, 1, 0)


def test_plan_prune_of_only_a_torn_fragment_has_no_last_hash():
    plan = _plan(b"tail", RetentionPolicy(max_records=5))

    assert (plan.valid, plan.torn, plan.drop, plan.last_hash) == (0, 1, 0, None)


def test_retention_of_a_ledger_that_is_only_a_torn_fragment_empties_it(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    target.write_bytes(b"garbage")

    removed = apply_retention_to_jsonl(target, RetentionPolicy(max_records=1))

    assert removed == 1
    assert target.read_bytes() == b""
    anchor = read_anchor(target)
    assert anchor is not None
    assert anchor.hash == GENESIS_PREV_HASH


def test_retention_of_an_empty_ledger_removes_nothing(tmp_path: Path):
    target = tmp_path / "audit.jsonl"
    target.write_bytes(b"")

    assert apply_retention_to_jsonl(target, RetentionPolicy(max_records=1)) == 0
    assert target.read_bytes() == b""

