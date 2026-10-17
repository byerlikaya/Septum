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
* The session ends when a decision arrives, when the optional timeout
  elapses or when the waiting task is cancelled (client disconnect); the
  session is removed in every case. Sessions nobody waits on are swept
  once they outlive the approval timeout.

All state is kept in memory and scoped to the current process; no raw PII or
anonymization maps are persisted or exposed. A session holds a private copy
of its turn's anonymization map only so the prompt preview can gate edited
chunks exactly like the send path does.
"""

import asyncio
import copy
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

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
    # Snapshot of the parameters needed to rebuild the assembled user
    # prompt (sanitized_query, regulation_names, language, output_mode,
    # document_id, query_has_placeholder). Stored here so the approval
    # router's preview-prompt endpoint can re-run ``_assemble_user_prompt``
    # with freshly edited chunks without the chat handler having to hold
    # any request-scoped state.
    assembly_context: Optional[Dict[str, Any]] = field(default=None)
    # User who initiated the approval. Approve/reject/preview-prompt
    # endpoints reject mismatched callers so a leaked session_id (logs,
    # SSE retry) cannot be used by a different user to influence which
    # masked chunks land in the cloud LLM. ``None`` means "no binding"
    # — only used by legacy code paths and the open_session helper.
    owner_user_id: Optional[int] = None
    # Private copy of the turn's anonymization map at the time the chunks
    # were offered. Never returned as-is: previews work on a fresh copy.
    anon_map: Optional[Any] = field(default=None, repr=False)
    # Number of tasks currently inside ``wait_for_decision`` for this
    # session; a session with a waiter is never swept.
    waiters: int = 0
    # ``time.monotonic()`` of the last accepted prompt preview.
    last_preview_at: Optional[float] = None


DEFAULT_ABANDONED_SESSION_SECONDS = 300.0


class ApprovalSessionForbiddenError(PermissionError):
    """Raised when a session operation is attempted by the wrong user."""


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

    async def create(
        self,
        session_id: str,
        masked_prompt: str,
        masked_chunks: List[str],
        entity_count: int,
        assembly_context: Optional[Dict[str, Any]] = None,
        owner_user_id: Optional[int] = None,
        anon_map: Optional[Any] = None,
        abandon_after_seconds: Optional[float] = None,
    ) -> _ApprovalSession:
        """Open a session using masked prompt/chunks.

        Before the new session is stored, sessions nobody is waiting on and
        older than ``abandon_after_seconds`` (the approval timeout; a
        non-positive or missing value falls back to
        :data:`DEFAULT_ABANDONED_SESSION_SECONDS`) are dropped, so a chat
        stream that died before it started waiting cannot leave its map
        copy behind.

        ``anon_map`` is the turn's anonymization map; the gate keeps its own
        copy so later preview requests gate edits against the same state the
        send path will use.

        Acquires ``self._lock`` around the dict mutation so a concurrent
        chat turn (SSE retry, replay) cannot race with in-flight
        readers and cause a half-formed session to be observed.

        ``owner_user_id`` binds the session to the user who initiated
        it. The approval router checks this before approve/reject/
        preview-prompt so a leaked session id cannot be used by another
        authenticated user to influence which masked chunks land in
        the cloud LLM. A session id already held by a different owner
        is never replaced: :class:`ApprovalSessionForbiddenError` is
        raised instead, so one user cannot hijack another's pending
        approval by reusing its id. When the same owner replaces a session,
        the old one drops its map copy and its waiter is released (it
        resolves as rejected with reason ``superseded``), so nothing keeps the old map alive.

        Returns the stored session as an opaque handle for
        :meth:`discard_session`.
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
            assembly_context=assembly_context,
            owner_user_id=owner_user_id,
            anon_map=copy.deepcopy(anon_map) if anon_map is not None else None,
        )

        async with self._lock:
            self._sweep_abandoned_locked(abandon_after_seconds)
            existing = self._sessions.get(session_id)
            if existing is not None:
                if existing.owner_user_id != owner_user_id:
                    raise ApprovalSessionForbiddenError(
                        f"Approval session {session_id!r} belongs to another owner"
                    )
                logger.warning("Replacing existing approval session: %s", session_id)
                if existing.decision is None:
                    existing.decision = ApprovalDecision(
                        approved=False, chunks=[], reason="superseded"
                    )
                existing.anon_map = None
                existing.event.set()
            self._sessions[session_id] = session
        return session

    def discard_session(self, session_id: str, handle: _ApprovalSession) -> None:
        """Drop ``handle`` if it is still the session stored under ``session_id``.

        For a chat stream that ends before (or while) waiting for the
        decision. A replacement stored under the same id is left alone.
        Synchronous, so it is safe in a ``finally`` that runs on
        cancellation; the dict mutation is atomic on the event loop.
        """
        if self._sessions.get(session_id) is handle:
            del self._sessions[session_id]
        handle.anon_map = None
        handle.event.set()

    async def claim_preview(self, session_id: str, min_interval_seconds: float) -> bool:
        """Return ``False`` if a preview for the session ran too recently.

        Otherwise records the preview time and returns ``True``. Raises
        :class:`ApprovalSessionNotFoundError` for an unknown session.
        """
        now = time.monotonic()
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise ApprovalSessionNotFoundError(
                    f"Approval session not found for id={session_id!r}"
                )
            last = session.last_preview_at
            if last is not None and now - last < min_interval_seconds:
                return False
            session.last_preview_at = now
            return True

    async def get_assembly_context(
        self, session_id: str
    ) -> Optional[Dict[str, Any]]:
        """Return the stored prompt-assembly context for a session.

        Used by the preview-prompt endpoint to rebuild the assembled user
        prompt when the user edits chunks in the approval modal. Returns
        ``None`` if the session does not exist or has no stored context.
        """
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            return (
                dict(session.assembly_context)
                if session.assembly_context is not None
                else None
            )

    async def get_preview_state(
        self, session_id: str
    ) -> Optional[Tuple[Dict[str, Any], List[str], Optional[Any]]]:
        """Return ``(assembly_context, offered_texts, anon_map_copy)`` for a preview.

        ``anon_map_copy`` is a fresh copy on every call, so gating edited
        chunks for a preview never changes the session's map. Returns
        ``None`` if the session does not exist or has no stored context.
        """
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.assembly_context is None:
                return None
            context = dict(session.assembly_context)
            offered = [c.text for c in session.chunks]
            anon_map = session.anon_map
        return context, offered, copy.deepcopy(anon_map) if anon_map is not None else None

    async def assert_session_owner(
        self, session_id: str, user_id: int, *, allow_admin_bypass: bool = False
    ) -> None:
        """Raise ``ApprovalSessionForbiddenError`` if ``user_id`` does not own the session.

        ``allow_admin_bypass`` lets the caller skip the check when the
        request is from an admin (the auth middleware passes role).
        Sessions opened without an owner (legacy code paths) are
        treated as bound to no one — every non-admin caller is rejected.
        """
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise ApprovalSessionNotFoundError(
                    f"Approval session not found for id={session_id!r}"
                )
            owner = session.owner_user_id
        if allow_admin_bypass:
            return
        if owner is None or owner != user_id:
            raise ApprovalSessionForbiddenError(
                f"User {user_id} is not the owner of session {session_id!r}"
            )

    async def wait_for_decision(
        self,
        session_id: str,
        timeout: Optional[float] = None,
    ) -> ApprovalDecision:
        """Wait for a user decision (or a timeout) for the given session.

        This coroutine blocks the calling task until one of the following
        happens:

        * :meth:`approve` is called for ``session_id``.
        * :meth:`reject` is called for ``session_id``.
        * The optional ``timeout`` (in seconds) elapses, in which case the
          session is auto-rejected with ``timed_out=True``.

        A non-positive ``timeout`` (``None``, ``0``, negative) disables the
        timeout entirely and waits indefinitely. Callers that want to bound
        the wait should pass a positive value (e.g. the user-configurable
        ``settings.approval_timeout_seconds``).

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
            session.waiters += 1
            event = session.event

        timed_out = False
        try:
            if timeout is not None and timeout > 0:
                try:
                    await asyncio.wait_for(event.wait(), timeout=timeout)
                except asyncio.TimeoutError:
                    timed_out = True
                    logger.warning(
                        "Approval session %s timed out after %.1fs",
                        session_id,
                        timeout,
                    )
            else:
                await event.wait()
        finally:
            # Runs on cancellation too (SSE client gone), so an abandoned
            # session never keeps its map copy alive. No await here: the
            # cleanup is atomic on the event loop and cannot be interrupted
            # by a second cancellation while waiting for ``self._lock``.
            session.waiters -= 1
            if self._sessions.get(session_id) is session:
                del self._sessions[session_id]
            session.anon_map = None

        if timed_out:
            # Session was removed above; even if a late decision arrives via
            # approve/reject it will hit ApprovalSessionNotFoundError, which
            # the HTTP layer surfaces as 404 to the user.
            return ApprovalDecision(
                approved=False,
                chunks=[],
                reason="approval-timeout",
                timed_out=True,
            )

        if session.decision is None:
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

    async def wait_for_approval(
        self,
        session_id: str,
        timeout: Optional[float] = None,
    ) -> ApprovalDecision:
        """Legacy wrapper that waits for a decision and returns it.

        Older call sites may expect a simple approval gate named
        ``wait_for_approval``. The richer :class:`ApprovalDecision` object is
        returned so that newer code can still access edited chunks.
        """
        return await self.wait_for_decision(session_id, timeout=timeout)

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
            session.anon_map = None
            session.event.set()

    def _sweep_abandoned_locked(self, max_age_seconds: Optional[float]) -> None:
        """Drop sessions without a waiter older than ``max_age_seconds``.

        Caller must hold ``self._lock``.
        """
        max_age = (
            max_age_seconds
            if max_age_seconds is not None and max_age_seconds > 0
            else DEFAULT_ABANDONED_SESSION_SECONDS
        )
        now = datetime.now(tz=timezone.utc)
        stale = [
            sid
            for sid, session in self._sessions.items()
            if session.waiters == 0
            and (now - session.created_at).total_seconds() > max_age
        ]
        for sid in stale:
            self._sessions.pop(sid).anon_map = None
        if stale:
            logger.info("Dropped %d abandoned approval sessions", len(stale))


_default_gate = ApprovalGate()


def get_approval_gate() -> ApprovalGate:
    """Return the process-wide default :class:`ApprovalGate` instance."""
    return _default_gate

