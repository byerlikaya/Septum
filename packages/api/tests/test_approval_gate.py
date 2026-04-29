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
