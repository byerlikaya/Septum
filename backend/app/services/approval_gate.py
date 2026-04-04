from __future__ import annotations

"""
User-approval gate for retrieved RAG chunks.

This module provides an in-memory coordination primitive that allows the
retrieval → LLM pipeline to pause and wait for an explicit user decision
before any sanitized chunks are sent to a cloud model.

Design overview
---------------
* Each approval session is identified by a caller-supplied ``session_id``,
  typically a chat/request identifier.
* For every session an :class:`asyncio.Event` is created; the pipeline awaits
  this event via :meth:`ApprovalGate.wait_for_decision`.
* The frontend receives the candidate chunks (for example via SSE) and
  responds through the HTTP layer, which calls :meth:`ApprovalGate.approve`
  or :meth:`ApprovalGate.reject`.
* The session waits indefinitely until a decision is received.

All state is kept in memory and scoped to the current process; no raw PII or
anonymization maps are persisted or exposed.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ApprovalChunk:
    """Lightweight representation of a sanitized chunk subject to approval."""

    id: Optional[int]
    document_id: Optional[int]
    text: str
    source_page: Optional[int] = None
    source_slide: Optional[int] = None
    source_sheet: Optional[int] = None
    source_timestamp_start: Optional[float] = None
    source_timestamp_end: Optional[float] = None
    section_title: Optional[str] = None


@dataclass
class ApprovalDecision:
    """Final decision for an approval session."""

    approved: bool
    chunks: List[ApprovalChunk]
    reason: Optional[str] = None
    timed_out: bool = False


@dataclass
class _ApprovalSession:
    """Internal representation of a pending approval session."""

    session_id: str
    chunks: List[ApprovalChunk]
    created_at: datetime
    event: asyncio.Event
    decision: Optional[ApprovalDecision] = None
    masked_prompt: Optional[str] = None
    entity_count: Optional[int] = None


class ApprovalSessionNotFoundError(KeyError):
    """Raised when an operation is attempted on a non-existent session."""


class ApprovalGate:
    """In-memory coordination primitive for user approvals.

    The gate exposes a small API:

    * :meth:`open_session` – register a new approval session for a set of
      sanitized chunks.
    * :meth:`wait_for_decision` – await an approval or rejection.
    * :meth:`approve` / :meth:`reject` – invoked by the HTTP layer when the
      user confirms or rejects the chunks (optionally after editing).
    * :meth:`get_session_snapshot` – read-only view for diagnostics or
      polling-style UIs.
    """

    def __init__(self) -> None:
        self._sessions: Dict[str, _ApprovalSession] = {}
        self._lock = asyncio.Lock()

    async def open_session(
        self,
        session_id: str,
        chunks: List[ApprovalChunk],
    ) -> None:
        """Create or replace an approval session for the given chunks.

        Parameters
        ----------
        session_id:
            Unique identifier for the approval session. Callers are
            responsible for ensuring stability (for example, per chat turn).
        chunks:
            Sanitized chunks that require explicit user approval before they
            can be sent to a cloud LLM.
        """
        event = asyncio.Event()
        created_at = datetime.now(tz=timezone.utc)
        session = _ApprovalSession(
            session_id=session_id,
            chunks=list(chunks),
            created_at=created_at,
            event=event,
        )

        async with self._lock:
            if session_id in self._sessions:
                logger.warning("Replacing existing approval session: %s", session_id)
            self._sessions[session_id] = session

    def create(
        self,
        session_id: str,
        masked_prompt: str,
        masked_chunks: List[str],
        entity_count: int,
    ) -> None:
        """Legacy helper to open a session using masked prompt/chunks.

        This synchronous method is intended for simple scripts and older code
        paths. It creates the approval session immediately on the current
        event loop so that a subsequent ``wait_for_approval`` call can always
        find it.
        """
        chunks: List[ApprovalChunk] = [
            ApprovalChunk(id=None, document_id=None, text=chunk)
            for chunk in masked_chunks
        ]
        event = asyncio.Event()
        created_at = datetime.now(tz=timezone.utc)
        session = _ApprovalSession(
            session_id=session_id,
            chunks=list(chunks),
            created_at=created_at,
            event=event,
            masked_prompt=masked_prompt,
            entity_count=entity_count,
        )

        # Replacing an existing session is allowed but logged.
        if session_id in self._sessions:
            logger.warning("Replacing existing approval session: %s", session_id)
        self._sessions[session_id] = session

    async def wait_for_decision(self, session_id: str) -> ApprovalDecision:
        """Wait for a user decision or timeout for the given session.

        This coroutine blocks the calling task until one of the following
        happens:

        * :meth:`approve` is called for ``session_id``.
        * :meth:`reject` is called for ``session_id``.

        Returns
        -------
        ApprovalDecision
            The final decision, including the (possibly edited) chunks.
        """
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise ApprovalSessionNotFoundError(
                    f"Approval session not found for id={session_id!r}"
                )
            event = session.event

        await event.wait()

        async with self._lock:
            session = self._sessions.pop(session_id, None)

        if session is None or session.decision is None:
            # As a safety net, treat missing decisions as a hard rejection.
            logger.error(
                "Approval session %s completed without a decision; treating as rejected.",
                session_id,
            )
            return ApprovalDecision(
                approved=False,
                chunks=[],
                reason="missing-decision",
                timed_out=False,
            )

        return session.decision

    async def wait_for_approval(self, session_id: str) -> ApprovalDecision:
        """Legacy wrapper that waits for a decision and returns it.

        Older call sites may expect a simple approval gate named
        ``wait_for_approval``. The richer :class:`ApprovalDecision` object is
        returned so that newer code can still access edited chunks.
        """
        return await self.wait_for_decision(session_id)

    # ------------------------------------------------------------------
    # User actions
    # ------------------------------------------------------------------
    async def approve(
        self,
        session_id: str,
        edited_chunks: List[ApprovalChunk],
    ) -> ApprovalDecision:
        """Mark a session as approved with the (possibly edited) chunks."""
        decision = ApprovalDecision(
            approved=True,
            chunks=list(edited_chunks),
            reason=None,
            timed_out=False,
        )
        await self._set_decision(session_id, decision)
        return decision

    def confirm(self, session_id: str) -> None:
        """Legacy synchronous helper to approve a session without edits.

        The actual approval is scheduled as a background task on the current
        event loop and uses the chunks stored in the session.
        """

        async def _run() -> None:
            chunks = await self._get_chunks_snapshot(session_id)
            await self.approve(session_id=session_id, edited_chunks=chunks)

        loop = asyncio.get_running_loop()
        loop.create_task(_run())

    async def reject(
        self,
        session_id: str,
        reason: Optional[str] = None,
    ) -> ApprovalDecision:
        """Mark a session as rejected."""
        chunks = await self._get_chunks_snapshot(session_id)
        decision = ApprovalDecision(
            approved=False,
            chunks=chunks,
            reason=reason,
            timed_out=False,
        )
        await self._set_decision(session_id, decision)
        return decision

    # ------------------------------------------------------------------
    # Introspection helpers
    # ------------------------------------------------------------------
    async def get_session_snapshot(
        self,
        session_id: str,
    ) -> Optional[_ApprovalSession]:
        """Return a shallow copy of the current session state, if any."""
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            # Return a lightweight copy to avoid leaking internal event state.
            return _ApprovalSession(
                session_id=session.session_id,
                chunks=list(session.chunks),
                created_at=session.created_at,
                event=session.event,
                decision=session.decision,
            )

    async def _get_chunks_snapshot(self, session_id: str) -> List[ApprovalChunk]:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise ApprovalSessionNotFoundError(
                    f"Approval session not found for id={session_id!r}"
                )
            return list(session.chunks)

    async def _set_decision(
        self,
        session_id: str,
        decision: ApprovalDecision,
    ) -> None:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise ApprovalSessionNotFoundError(
                    f"Approval session not found for id={session_id!r}"
                )
            # Only the first decision wins; subsequent calls are ignored but logged.
            if session.decision is not None:
                logger.warning(
                    "Ignoring decision update for already-finalized session %s",
                    session_id,
                )
                return
            session.decision = decision
            session.event.set()


_default_gate = ApprovalGate()


def get_approval_gate() -> ApprovalGate:
    """Return the process-wide default :class:`ApprovalGate` instance."""
    return _default_gate

