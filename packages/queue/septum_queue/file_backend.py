"""File-system backed queue for air-gapped Septum deployments.

The backend stores one JSON payload per file across three sibling
directories:

    {root}/{topic}/incoming/    — newly published, not yet delivered
    {root}/{topic}/processing/  — delivered to a consumer, awaiting ack
    {root}/{topic}/done/        — acked; kept for audit / inspection.
                                  This backend never trims ``done/`` —
                                  operators must rotate it via cron
                                  (e.g. ``find done/ -mtime +7 -delete``)
                                  to bound disk usage.
    {root}/{topic}/dead-letter/ — quarantined entries (see below).

Atomic rename (``os.replace``) is the entire synchronization primitive.
It is POSIX-atomic inside a single filesystem, so moving a file from
``incoming/`` to ``processing/`` is how a consumer claims it: if two
consumers race, only one rename wins. No fcntl locks, no advisory
flags — the rename wins or fails, nothing in between.

Quarantine and size cap:

* An entry that cannot be read or decoded (larger than ``MAX_ENTRY_BYTES``,
  not a regular file, not UTF-8 JSON, not a JSON object, or nested too deeply)
  is moved to ``dead-letter/`` and never parsed past the size check. The
  consumer does not raise for it.
* An entry the OS refuses to claim is moved to ``dead-letter/`` too; if that
  move also fails, the entry is retried after a cooldown.
* Dead-letter names are unique (``{time_ns}-{uuid}-{name}``), so earlier
  evidence is never overwritten.
* A fault in the directories themselves (no write or search permission, disk
  full, read-only filesystem) raises ``QueueError`` and leaves the messages in
  ``incoming/``.

Why this instead of a network queue:

1. Zero infrastructure dependency. An air-gapped deployment that ships
   `septum-api` + `septum-gateway` on the same volume needs nothing else.
2. Inspectable. An operator debugging a stuck request can ``ls
   processing/`` and see exactly which correlation ids are in flight.
3. Durable by default. A crash mid-processing leaves the payload in
   ``processing/`` where the next consumer sweep picks it up (at-
   least-once; callers must be idempotent or dedupe on correlation_id).

Limits:

* Does NOT scale beyond one producer-consumer pair per topic directory.
  For shared-infrastructure deployments with multiple consumers use the
  Redis streams backend.
* Directory scans are O(incoming). That is fine at chat throughput
  (tens-to-hundreds of pending items) but would hurt at batch scale.
"""

from __future__ import annotations

import asyncio
import errno
import json
import logging
import os
import re
import stat
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Literal, Mapping

from .base import MAX_ENTRY_BYTES, QueueBackend, QueueError
from .models import Message

logger = logging.getLogger(__name__)

_SAFE_TOPIC_RE = re.compile(r"^[A-Za-z0-9._-]+$")

_READ_CHUNK_BYTES = 64 * 1024

# Seconds a refused entry is left alone before it is tried again.
_REFUSAL_COOLDOWN_SECONDS = 60

# Refusal log budget: at most this many per-name lines per window, then one summary.
_REFUSAL_LOG_WINDOW_SECONDS = 60
_REFUSAL_LOG_NAMES_PER_WINDOW = 10

# Permission refusals within one pass, with no successful claim, mean the directory is at fault.
_REPEATED_ACCESS_REFUSALS = 3

# errnos that mean the filesystem or directory failed, not the entry being renamed.
_DIRECTORY_FAULT_ERRNOS = frozenset(
    {errno.ENOSPC, errno.EDQUOT, errno.EROFS, errno.EIO, errno.EXDEV}
)

_ClaimFailure = Literal["lost", "retry", "directory", "entry"]


def _validate_topic(topic: str) -> None:
    """Reject topics that would escape the queue root via path traversal.

    ``Path(root) / topic`` does NOT strip leading slashes — ``"a" / "/etc/x"``
    resolves to ``"/etc/x"`` on POSIX — and ``..`` segments climb out of
    ``root``. Reject anything that is not a flat alphanumeric token.
    """
    if not topic or not _SAFE_TOPIC_RE.fullmatch(topic):
        raise QueueError(
            f"invalid topic {topic!r}: must match ^[A-Za-z0-9._-]+$"
        )


def _read_capped(fd: int, limit: int) -> bytes:
    """Read from ``fd`` until EOF or ``limit`` bytes, whichever comes first."""
    chunks: list[bytes] = []
    total = 0
    while total < limit:
        chunk = os.read(fd, min(_READ_CHUNK_BYTES, limit - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def _unique_quarantine_name(name: str) -> str:
    """A dead-letter name that never collides with, or overwrites, earlier evidence.

    The original name is cut to 64 bytes on a character boundary, so the result
    stays within the 255-byte filename limit whatever the original's encoding.
    """
    kept = os.fsencode(name)[:64].decode("utf-8", errors="ignore")
    return f"{time.time_ns():020d}-{uuid.uuid4().hex}-{kept}"


class _OversizedEntryError(ValueError):
    """Raised when a claimed entry is larger than ``MAX_ENTRY_BYTES``."""


class _NotRegularFileError(ValueError):
    """Raised when a claimed entry is not a regular file (FIFO, device, socket)."""


class _NotAnObjectError(ValueError):
    """Raised when a claimed entry decodes to JSON that is not an object."""


@dataclass
class _PassState:
    """Counters for one consume pass. Created per pass, so concurrent consumers never share them."""

    access_refusals: int = 0
    claims: int = 0


def _log_failure_after_cancel(task: asyncio.Future) -> None:
    """Log a failed call that finished after its caller was cancelled, by type only."""
    if task.cancelled() or task.exception() is None:
        return
    logger.error(
        "Queue operation failed after the caller was cancelled: %s",
        type(task.exception()).__name__,
    )


async def _run_shielded(awaitable, on_cancel_done=None):
    """Await a queue thread call; a cancellation returns at once and the call still runs.

    The inner task is created before the first await, so the thread is submitted even
    when the caller is cancelled first. A cancelled caller raises ``CancelledError``
    immediately and never waits for a stalled thread. The call's result is then
    discarded; ``on_cancel_done`` (default: log a failure by type) runs when it finishes.
    """
    task = asyncio.ensure_future(awaitable)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        task.add_done_callback(on_cancel_done or _log_failure_after_cancel)
        raise


def _fsync_dir(path: Path) -> None:
    """Best-effort fsync of a directory entry so a rename survives a crash.

    On platforms without ``O_DIRECTORY`` support (Windows) the open call
    raises and we skip — durability degrades gracefully there but the
    queue is already labelled POSIX-only in the docstring.
    """
    try:
        fd = os.open(path, os.O_DIRECTORY)
    except (NotImplementedError, OSError):
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class FileQueueBackend(QueueBackend):
    """POSIX-rename-based queue suitable for single-host air-gapped installs."""

    topic: str

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        topic: str,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """``clock`` drives the consume deadline and the refusal cooldown and log window."""
        _validate_topic(topic)
        self.topic = topic
        self._root = Path(root) / topic
        self._incoming = self._root / "incoming"
        self._processing = self._root / "processing"
        self._done = self._root / "done"
        self._dead_letter = self._root / "dead-letter"
        for directory in (
            self._incoming,
            self._processing,
            self._done,
            self._dead_letter,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        # name -> clock time until which a refused entry is left alone. Pruned to the
        # names still listed in incoming/ on every pass, so it cannot grow unbounded.
        self._refused_until: dict[str, float] = {}
        # Refusal log window: when it started, refusals in it, and per-name lines logged.
        self._window_started: float | None = None
        self._window_refusals = 0
        self._window_logged = 0
        # Recover orphaned messages from a previous crash. Anything in
        # ``processing/`` was claimed by a now-dead consumer; the
        # at-least-once promise needs us to put those back in
        # ``incoming/`` for the next consume cycle.
        self._reclaim_orphans()
        self._closed = False
        self._background: set[asyncio.Task] = set()

    def _reclaim_orphans(self) -> None:
        try:
            entries = list(self._processing.iterdir())
        except FileNotFoundError:
            return
        for entry in entries:
            if entry.suffix != ".json":
                continue
            target = self._incoming / entry.name
            if os.path.lexists(target):
                # A same-named entry is already waiting; keep both under a fresh name
                # rather than overwrite it.
                target = self._incoming / f"{time.time_ns():020d}-{uuid.uuid4().hex}.json"
            try:
                os.replace(entry, target)
                logger.warning(
                    "Recovered orphaned queue message %s from a previous crash",
                    repr(entry.name)[:64],
                )
            except OSError:
                continue
        _fsync_dir(self._incoming)
        _fsync_dir(self._processing)

    def _ensure_open(self) -> None:
        if self._closed:
            raise QueueError("FileQueueBackend has been closed")

    async def publish(self, payload: Mapping[str, object]) -> str:
        """Atomically place a new payload in ``incoming/``.

        The filename is ``{monotonic_ns}-{uuid4hex}.json`` so FIFO
        ordering falls out of a lexicographic directory scan.
        """
        self._ensure_open()
        return await asyncio.to_thread(self._publish_sync, dict(payload))

    def _publish_sync(self, payload: dict[str, object]) -> str:
        # ``time.time_ns`` is a wall-clock counter that survives process
        # restart — ``time.monotonic_ns`` resets per process and made
        # the FIFO ordering claim a lie after the producer cycled.
        name = f"{time.time_ns():020d}-{uuid.uuid4().hex}.json"
        final = self._incoming / name
        # Write to a sibling tmp file first, fsync it, atomic-rename
        # into place, and fsync the parent directory so a power loss
        # cannot lose the publish even though the rename appeared to
        # succeed in-cache.
        tmp = final.with_suffix(".json.tmp")
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        with open(tmp, "wb") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, final)
        _fsync_dir(self._incoming)
        return name

    async def consume(
        self,
        *,
        batch_size: int = 1,
        block_ms: int | None = None,
    ) -> AsyncIterator[Message]:
        """Yield incoming messages, claiming each by atomic rename.

        Polls ``incoming/`` every 50 ms when ``block_ms`` is non-zero.
        ``block_ms=0`` disables polling and returns immediately when the
        queue is empty — useful for unit tests. ``block_ms=None``
        polls indefinitely.
        """
        self._ensure_open()
        deadline = None
        if block_ms is not None and block_ms > 0:
            deadline = self._clock() + (block_ms / 1000.0)

        yielded = 0
        while yielded < batch_size:
            state = _PassState()
            names = await asyncio.to_thread(self._incoming_names)
            for name in names:
                if yielded >= batch_size:
                    return
                message = await self._claim_and_load(name, state)
                if message is None:
                    # Quarantined entries do not count toward batch_size. Check the
                    # deadline here too, so a backlog of bad entries cannot overrun it.
                    if deadline is not None and self._clock() >= deadline:
                        return
                    continue
                yielded += 1
                yield message

            # Empty-queue branch: nothing was claimed in this pass, whether the
            # listing was empty or every claim lost a race or was refused.
            if state.claims == 0:
                if block_ms == 0:
                    return
                if deadline is not None and self._clock() >= deadline:
                    return
                await asyncio.sleep(0.05)

    async def _claim_and_load(self, name: str, state: _PassState) -> Message | None:
        """Claim and decode one entry in a thread; ``None`` when it is not delivered.

        A cancelled caller raises ``CancelledError`` at once. The thread finishes on its
        own, and an entry it claimed is handed back to ``incoming/`` in the background.
        That holds only while the event loop runs: a ``close()`` or loop shutdown can
        leave one entry in ``processing/``, which ``_reclaim_orphans`` returns at the
        next start.
        """
        return await _run_shielded(
            asyncio.to_thread(self._claim_and_load_sync, name, state),
            on_cancel_done=self._hand_back_when_done,
        )

    def _claim_and_load_sync(self, name: str, state: _PassState) -> Message | None:
        """Claim one entry and decode it; runs in a worker thread.

        An unexpected failure after the claim returns the entry to ``incoming/``
        before the original exception propagates.
        """
        if not self._claim(name, state):
            return None
        try:
            return self._load_claimed(name)
        except Exception:
            self._hand_back_after_load_failure(name)
            raise

    def _hand_back_after_load_failure(self, name: str) -> None:
        """Requeue a claimed entry after a load failure; a failure here is logged, never raised."""
        try:
            self._nack_sync(name, True)
        except Exception as exc:
            logger.error(
                "Could not hand back queue message %s after a load failure: %s",
                repr(name)[:64],
                type(exc).__name__,
            )

    def _hand_back_when_done(self, task: asyncio.Future[Message | None]) -> None:
        """Start the hand-back of a claim that finished after its consumer was cancelled."""
        if task.cancelled() or task.exception() is not None:
            _log_failure_after_cancel(task)
            return
        message = task.result()
        if message is None:
            return
        background = asyncio.ensure_future(self._hand_back(message.id))
        self._background.add(background)
        background.add_done_callback(self._background.discard)

    async def _hand_back(self, message_id: str) -> None:
        """Return a claimed entry to ``incoming/``; a failure is logged, never raised."""
        try:
            await _run_shielded(asyncio.to_thread(self._nack_sync, message_id, True))
        except Exception as exc:
            logger.error(
                "Could not hand back queue message %s after cancellation: %s",
                repr(message_id)[:64],
                type(exc).__name__,
            )

    def _incoming_names(self) -> list[str]:
        """Sorted ``.json`` names in ``incoming/``, read once per consume pass."""
        try:
            entries = sorted(self._incoming.iterdir())
        except FileNotFoundError:
            entries = []
        names = [entry.name for entry in entries if entry.suffix == ".json"]
        listed = set(names)
        for gone in list(self._refused_until):
            if gone not in listed:
                self._refused_until.pop(gone, None)
        return names

    def _claim(self, name: str, state: _PassState) -> bool:
        """Atomically rename one incoming file into processing/.

        Returns False when another consumer won the race, or when this entry was
        refused and is cooling down. A directory-level fault raises ``QueueError``.
        An entry-specific refusal is quarantined, and if even that fails the entry
        is retried after a cooldown.
        """
        retry_at = self._refused_until.get(name)
        if retry_at is not None:
            if self._clock() < retry_at:
                return False
            self._refused_until.pop(name, None)
        source = self._incoming / name
        try:
            os.replace(source, self._processing / name)
        except OSError as exc:
            category = self._classify_claim_failure(exc, source)
            if category == "directory":
                raise QueueError(
                    "cannot claim queue messages: incoming/ or processing/ is unusable "
                    f"({type(exc).__name__})"
                ) from exc
            if category != "entry":
                return False  # "lost" or "retry"
            # Only a refusal of one of our own regular files counts toward a directory
            # fault. A read-only subdirectory, or a file another uid owns in a sticky
            # incoming/, is an entry fault and takes the quarantine/cooldown path.
            if exc.errno in (errno.EACCES, errno.EPERM) and self._is_worker_regular_file(source):
                state.access_refusals += 1
                if state.access_refusals >= _REPEATED_ACCESS_REFUSALS and state.claims == 0:
                    self._refused_until[name] = self._clock() + _REFUSAL_COOLDOWN_SECONDS
                    raise QueueError(
                        "cannot claim queue messages: repeated permission refusals "
                        f"in one pass ({type(exc).__name__})"
                    ) from exc
            self._quarantine_refused(name, exc)
            return False
        state.claims += 1
        return True

    @staticmethod
    def _is_worker_regular_file(path: Path) -> bool:
        """True for a regular file this worker's uid owns (``lstat``: a symlink is not one)."""
        try:
            info = os.lstat(path)
        except OSError:
            return False
        return stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()

    def _classify_claim_failure(self, exc: OSError, source: Path) -> _ClaimFailure:
        """Say whom a failed claim rename belongs to: another consumer, a directory, or the entry."""
        if isinstance(exc, FileNotFoundError) and not os.path.lexists(source):
            return "lost"
        if self._is_directory_fault(exc, self._processing) or self._is_directory_fault(
            exc, self._incoming
        ):
            return "directory"
        if isinstance(exc, FileNotFoundError):
            return "retry"
        return "entry"

    @staticmethod
    def _is_directory_fault(exc: OSError, directory: Path) -> bool:
        """True when the failure belongs to ``directory``, not to this one entry."""
        if exc.errno in _DIRECTORY_FAULT_ERRNOS:
            return True
        try:
            info = os.stat(directory)
        except OSError:
            return True
        # A rename into or out of a directory needs write and search (execute) on it.
        return not (
            stat.S_ISDIR(info.st_mode) and os.access(directory, os.W_OK | os.X_OK)
        )

    def _move_to_dead_letter(self, source: Path, name: str) -> OSError | None:
        """Move ``source`` to ``dead-letter/`` under a unique name.

        Returns None on success. On failure returns the ``OSError`` so the caller
        applies its own policy.
        """
        try:
            os.replace(source, self._dead_letter / _unique_quarantine_name(name))
        except OSError as exc:
            return exc
        return None

    def _quarantine_refused(self, name: str, claim_exc: OSError) -> None:
        """Quarantine an entry the OS refused to claim; on failure, cool it down."""
        source = self._incoming / name
        error = self._move_to_dead_letter(source, name)
        if error is None:
            logger.error(
                "Quarantined queue message %s that could not be claimed: %s",
                repr(name)[:64],
                type(claim_exc).__name__,
            )
            return
        if isinstance(error, FileNotFoundError) and not os.path.lexists(source):
            return  # Already claimed by another consumer; nothing to remember.
        if self._is_directory_fault(error, self._dead_letter):
            raise QueueError(
                "cannot quarantine queue messages: dead-letter/ is unusable "
                f"({type(error).__name__})"
            ) from error
        self._refused_until[name] = self._clock() + _REFUSAL_COOLDOWN_SECONDS
        self._record_refusal(name, error)

    def _record_refusal(self, name: str, exc: BaseException) -> None:
        """Log a refused entry within the window's budget.

        Each window (``_REFUSAL_LOG_WINDOW_SECONDS``, on the injectable clock) logs
        at most ``_REFUSAL_LOG_NAMES_PER_WINDOW`` per-name lines, so a rename storm
        cannot flood the log. A single summary line for the window is written when
        the next window starts. Logging only names and exception types keeps the
        output bounded in both count and content.
        """
        now = self._clock()
        if self._window_started is None or now - self._window_started >= _REFUSAL_LOG_WINDOW_SECONDS:
            self._flush_refusal_window()
            self._window_started = now
            self._window_refusals = 0
            self._window_logged = 0
        self._window_refusals += 1
        if self._window_logged >= _REFUSAL_LOG_NAMES_PER_WINDOW:
            return
        self._window_logged += 1
        logger.error(
            "Could not claim queue message %s; retrying after %ds: %s",
            repr(name)[:64],
            _REFUSAL_COOLDOWN_SECONDS,
            type(exc).__name__,
        )

    def _flush_refusal_window(self) -> None:
        """Write the summary for the window that just ended, if it refused anything."""
        if self._window_refusals == 0:
            return
        logger.error(
            "%d queue message refusal(s) in the last %ds; %d per-name line(s) suppressed",
            self._window_refusals,
            _REFUSAL_LOG_WINDOW_SECONDS,
            self._window_refusals - self._window_logged,
        )

    def _load_claimed(self, name: str) -> Message | None:
        """Decode a claimed file, or quarantine it and return ``None``.

        Any entry that cannot be read or decoded is moved to ``dead-letter/`` so it
        cannot wedge the consumer or come back on restart. Only the entry's name and
        exception type are logged.
        """
        path = self._processing / name
        try:
            payload = self._read_entry(path)
        except (ValueError, RecursionError, OSError) as exc:
            error = self._move_to_dead_letter(path, name)
            if error is None:
                logger.error(
                    "Quarantined undecodable queue message %s: %s",
                    repr(name)[:64],
                    type(exc).__name__,
                )
            else:
                logger.error(
                    "Could not quarantine queue message %s: %s",
                    repr(name)[:64],
                    type(error).__name__,
                )
            return None
        return Message(id=name, payload=payload)

    @staticmethod
    def _read_entry(path: Path) -> dict[str, Any]:
        """Open, type-check, read and decode one claimed entry, raising on any defect.

        The file is opened with ``O_NOFOLLOW | O_NONBLOCK`` so a symlink is never
        followed and a FIFO cannot block the thread. The type check runs on the
        open descriptor, the read is capped at ``MAX_ENTRY_BYTES + 1`` bytes whatever
        the file size reports, and the descriptor is closed on every path.
        """
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise _NotRegularFileError()
            raw = _read_capped(fd, MAX_ENTRY_BYTES + 1)
        finally:
            os.close(fd)
        if len(raw) > MAX_ENTRY_BYTES:
            raise _OversizedEntryError()
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise _NotAnObjectError()
        return payload

    async def ack(self, message_id: str) -> None:
        """Move a claimed message from ``processing/`` to ``done/``."""
        self._ensure_open()
        await _run_shielded(asyncio.to_thread(self._ack_sync, message_id))

    def _ack_sync(self, message_id: str) -> None:
        src = self._processing / message_id
        if not src.exists():
            return  # Idempotent — a double-ack is a no-op.
        os.replace(src, self._done / message_id)
        _fsync_dir(self._done)
        _fsync_dir(self._processing)

    async def nack(self, message_id: str, *, requeue: bool = True) -> None:
        """Return a claimed message to ``incoming/`` or drop it."""
        self._ensure_open()
        await _run_shielded(asyncio.to_thread(self._nack_sync, message_id, requeue))

    def _nack_sync(self, message_id: str, requeue: bool) -> None:
        src = self._processing / message_id
        if not src.exists():
            return
        if requeue:
            # Mint a fresh timestamp so a poison message does not
            # land back at the same lex position and starve newer
            # entries — the original filename starts with the original
            # publish timestamp, which is older than every healthy
            # message, so a re-claim loop would re-pick it first.
            new_name = f"{time.time_ns():020d}-{uuid.uuid4().hex}.json"
            os.replace(src, self._incoming / new_name)
        else:
            error = self._move_to_dead_letter(src, message_id)
            if error is not None:
                raise error
        _fsync_dir(self._incoming)
        _fsync_dir(self._processing)
        _fsync_dir(self._dead_letter)

    async def close(self) -> None:
        """Mark the backend closed so subsequent operations error out.

        Directories are left in place — they are the durable queue
        state and the next process instance reuses them.
        """
        self._closed = True
