from __future__ import annotations

"""Unit tests for the in-memory ``ApprovalGate``.

These exercise the real gate (not the fake used by ``test_approval_router``)
so the timeout, approval, and rejection branches are all covered. The chat
SSE handler depends on ``wait_for_decision`` returning a ``timed_out=True``
decision instead of hanging forever when the user never clicks
Approve / Reject in the frontend.
"""

import asyncio

import pytest
from septum_api.services.approval_gate import (
    ApprovalChunk,
    ApprovalGate,
    ApprovalSessionForbiddenError,
    ApprovalSessionNotFoundError,
)


def _make_chunks() -> list[ApprovalChunk]:
    return [
        ApprovalChunk(id=1, document_id=1, text="masked chunk one"),
        ApprovalChunk(id=2, document_id=1, text="masked chunk two"),
    ]


@pytest.mark.asyncio
async def test_wait_for_decision_returns_approval() -> None:
    gate = ApprovalGate()
    session_id = "approval-session"
    await gate.create(
        session_id=session_id,
        masked_prompt="What is X?",
        masked_chunks=["masked chunk one", "masked chunk two"],
        entity_count=0,
    )

    edited = _make_chunks()

    async def _approve_soon() -> None:
        await asyncio.sleep(0.01)
        await gate.approve(session_id=session_id, edited_chunks=edited)

    waiter = asyncio.create_task(
        gate.wait_for_decision(session_id, timeout=2.0)
    )
    await _approve_soon()
    decision = await waiter

    assert decision.approved is True
    assert decision.timed_out is False
    assert [c.text for c in decision.chunks] == [
        "masked chunk one",
        "masked chunk two",
    ]


@pytest.mark.asyncio
async def test_wait_for_decision_times_out() -> None:
    gate = ApprovalGate()
    session_id = "timeout-session"
    await gate.create(
        session_id=session_id,
        masked_prompt="What is X?",
        masked_chunks=["masked chunk"],
        entity_count=0,
    )

    decision = await gate.wait_for_decision(session_id, timeout=0.05)

    assert decision.approved is False
    assert decision.timed_out is True
    assert decision.reason == "approval-timeout"
    assert decision.chunks == []

    # The session must have been popped, so a late approve raises NotFound.
    with pytest.raises(ApprovalSessionNotFoundError):
        await gate.approve(session_id=session_id, edited_chunks=_make_chunks())


@pytest.mark.asyncio
async def test_wait_for_decision_no_timeout_waits_indefinitely() -> None:
    """A timeout of 0 must mean ``wait forever``, not ``return immediately``."""
    gate = ApprovalGate()
    session_id = "infinite-session"
    await gate.create(
        session_id=session_id,
        masked_prompt="What is X?",
        masked_chunks=["masked chunk"],
        entity_count=0,
    )

    waiter = asyncio.create_task(
        gate.wait_for_decision(session_id, timeout=0)
    )

    # Give the waiter a chance to start; if timeout=0 was incorrectly treated
    # as ``return immediately`` the waiter would have already finished here.
    await asyncio.sleep(0.05)
    assert not waiter.done()

    await gate.approve(session_id=session_id, edited_chunks=_make_chunks())
    decision = await waiter

    assert decision.approved is True
    assert decision.timed_out is False


@pytest.mark.asyncio
async def test_wait_for_decision_rejection() -> None:
    gate = ApprovalGate()
    session_id = "reject-session"
    await gate.create(
        session_id=session_id,
        masked_prompt="What is X?",
        masked_chunks=["masked chunk"],
        entity_count=0,
    )

    async def _reject_soon() -> None:
        await asyncio.sleep(0.01)
        await gate.reject(session_id=session_id, reason="not-allowed")

    waiter = asyncio.create_task(
        gate.wait_for_decision(session_id, timeout=2.0)
    )
    await _reject_soon()
    decision = await waiter

    assert decision.approved is False
    assert decision.timed_out is False
    assert decision.reason == "not-allowed"


@pytest.mark.asyncio
async def test_create_refuses_to_replace_another_owners_session() -> None:
    gate = ApprovalGate()
    await gate.create(
        session_id="s", masked_prompt="first", masked_chunks=["a"], entity_count=0, owner_user_id=1
    )

    with pytest.raises(ApprovalSessionForbiddenError):
        await gate.create(
            session_id="s", masked_prompt="hijack", masked_chunks=["b"], entity_count=0, owner_user_id=2
        )

    await gate.assert_session_owner("s", 1)
    snapshot = await gate.get_session_snapshot("s")
    assert [c.text for c in snapshot.chunks] == ["a"]


@pytest.mark.asyncio
async def test_create_lets_the_owner_replace_their_session() -> None:
    gate = ApprovalGate()
    await gate.create(
        session_id="s", masked_prompt="first", masked_chunks=["a"], entity_count=0, owner_user_id=1
    )
    await gate.create(
        session_id="s", masked_prompt="again", masked_chunks=["b"], entity_count=0, owner_user_id=1
    )

    snapshot = await gate.get_session_snapshot("s")
    assert [c.text for c in snapshot.chunks] == ["b"]


async def _open_with_map(gate: ApprovalGate, session_id: str, **kwargs) -> None:
    from septum_api.services.anonymization_map import AnonymizationMap

    amap = AnonymizationMap(document_id=0, language="en")
    amap.entity_map["Alice"] = "[PERSON_1]"
    await gate.create(
        session_id=session_id,
        masked_prompt="q",
        masked_chunks=["[PERSON_1] signed."],
        entity_count=1,
        anon_map=amap,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_cancelled_wait_removes_the_session_and_its_map() -> None:
    gate = ApprovalGate()
    await _open_with_map(gate, "s-cancel")
    session = gate._sessions["s-cancel"]

    task = asyncio.create_task(gate.wait_for_decision("s-cancel", timeout=300))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert "s-cancel" not in gate._sessions
    assert session.anon_map is None
    with pytest.raises(ApprovalSessionNotFoundError):
        await gate.approve("s-cancel", edited_chunks=[])


@pytest.mark.asyncio
async def test_decision_clears_the_map_copy() -> None:
    gate = ApprovalGate()
    await _open_with_map(gate, "s-decided")
    session = gate._sessions["s-decided"]

    await gate.approve("s-decided", edited_chunks=[])

    assert session.anon_map is None
    decision = await gate.wait_for_decision("s-decided", timeout=1)
    assert decision.approved is True
    assert "s-decided" not in gate._sessions


@pytest.mark.asyncio
async def test_finished_wait_does_not_remove_a_replacing_session() -> None:
    gate = ApprovalGate()
    await _open_with_map(gate, "s-replaced")
    task = asyncio.create_task(gate.wait_for_decision("s-replaced", timeout=300))
    await asyncio.sleep(0)
    await _open_with_map(gate, "s-replaced")
    replacement = gate._sessions["s-replaced"]

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert gate._sessions["s-replaced"] is replacement
    assert replacement.anon_map is not None


@pytest.mark.asyncio
async def test_abandoned_sessions_are_swept_after_the_timeout() -> None:
    from datetime import timedelta

    gate = ApprovalGate()
    await _open_with_map(gate, "s-old")
    old = gate._sessions["s-old"]
    old.created_at -= timedelta(seconds=120)
    await _open_with_map(gate, "s-waited")
    waited = gate._sessions["s-waited"]
    waited.created_at -= timedelta(seconds=120)
    waiter = asyncio.create_task(gate.wait_for_decision("s-waited", timeout=0))
    await asyncio.sleep(0)

    await _open_with_map(gate, "s-new", abandon_after_seconds=60)

    assert "s-old" not in gate._sessions
    assert old.anon_map is None
    assert "s-waited" in gate._sessions
    assert "s-new" in gate._sessions
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter


@pytest.mark.asyncio
async def test_young_sessions_survive_the_sweep() -> None:
    gate = ApprovalGate()
    await _open_with_map(gate, "s-young")

    await _open_with_map(gate, "s-next", abandon_after_seconds=60)

    assert "s-young" in gate._sessions


@pytest.mark.asyncio
async def test_owner_replacing_a_session_releases_the_old_waiter_and_map() -> None:
    gate = ApprovalGate()
    await _open_with_map(gate, "s-superseded", owner_user_id=1)
    old = gate._sessions["s-superseded"]
    task = asyncio.create_task(gate.wait_for_decision("s-superseded", timeout=300))
    await asyncio.sleep(0)

    await _open_with_map(gate, "s-superseded", owner_user_id=1)
    replacement = gate._sessions["s-superseded"]
    decision = await asyncio.wait_for(task, timeout=1)

    assert decision.approved is False
    assert decision.reason == "superseded"
    assert old.anon_map is None
    assert gate._sessions["s-superseded"] is replacement
    assert replacement.anon_map is not None


@pytest.mark.asyncio
async def test_discard_session_drops_only_its_own_session() -> None:
    gate = ApprovalGate()
    first = await gate.create(
        session_id="s-discard", masked_prompt="q", masked_chunks=["a"], entity_count=0
    )
    gate.discard_session("s-discard", first)
    assert "s-discard" not in gate._sessions

    stale = await gate.create(
        session_id="s-discard", masked_prompt="q", masked_chunks=["a"], entity_count=0
    )
    current = await gate.create(
        session_id="s-discard", masked_prompt="q", masked_chunks=["b"], entity_count=0
    )
    gate.discard_session("s-discard", stale)
    assert gate._sessions["s-discard"] is current


@pytest.mark.asyncio
async def test_claim_preview_refuses_a_second_preview_within_the_interval() -> None:
    gate = ApprovalGate()
    await gate.create(session_id="s-preview", masked_prompt="q", masked_chunks=["a"], entity_count=0)

    assert await gate.claim_preview("s-preview", 60) is True
    assert await gate.claim_preview("s-preview", 60) is False
    assert await gate.claim_preview("s-preview", 0) is True
    with pytest.raises(ApprovalSessionNotFoundError):
        await gate.claim_preview("missing", 60)
