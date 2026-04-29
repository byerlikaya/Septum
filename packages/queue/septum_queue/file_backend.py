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

Atomic rename (``os.replace``) is the entire synchronization primitive.
It is POSIX-atomic inside a single filesystem, so moving a file from
``incoming/`` to ``processing/`` is how a consumer claims it: if two
consumers race, only one rename wins. No fcntl locks, no advisory
flags — the rename wins or fails, nothing in between.

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
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import AsyncIterator, Mapping

from .base import QueueBackend, QueueError
from .models import Message

logger = logging.getLogger(__name__)

_SAFE_TOPIC_RE = re.compile(r"^[A-Za-z0-9._-]+$")


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

    def __init__(self, root: str | os.PathLike[str], *, topic: str) -> None:
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
        # Recover orphaned messages from a previous crash. Anything in
        # ``processing/`` was claimed by a now-dead consumer; the
        # at-least-once promise needs us to put those back in
        # ``incoming/`` for the next consume cycle.
        self._reclaim_orphans()
        self._closed = False

    def _reclaim_orphans(self) -> None:
        try:
            entries = list(self._processing.iterdir())
        except FileNotFoundError:
            return
        for entry in entries:
            if entry.suffix != ".json":
                continue
            target = self._incoming / entry.name
            try:
                os.replace(entry, target)
                logger.warning(
                    "Recovered orphaned queue message %s from a previous crash",
                    entry.name,
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
            deadline = time.monotonic() + (block_ms / 1000.0)

        yielded = 0
        while yielded < batch_size:
            name = await asyncio.to_thread(self._claim_next)
            if name is not None:
                message = await asyncio.to_thread(self._load_claimed, name)
                yielded += 1
                yield message
                continue

            if block_ms == 0:
                return
            if deadline is not None and time.monotonic() >= deadline:
                return
            await asyncio.sleep(0.05)

    def _claim_next(self) -> str | None:
        """Try to atomically rename one incoming file into processing/."""
        try:
            entries = sorted(self._incoming.iterdir())
        except FileNotFoundError:
            return None
        for entry in entries:
            if entry.suffix != ".json":
                continue
            target = self._processing / entry.name
            try:
                os.replace(entry, target)
                return entry.name
            except FileNotFoundError:
                # Another consumer won the race; try the next entry.
                continue
        return None

    def _load_claimed(self, name: str) -> Message:
        path = self._processing / name
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            # Quarantine corrupted entries instead of wedging the
            # consumer. Operators can inspect dead-letter/ to find a
            # producer regression rather than losing messages silently.
            dest = self._dead_letter / name
            try:
                os.replace(path, dest)
            except OSError:
                pass
            logger.error(
                "Quarantined corrupted queue message %s: %s",
                name,
                type(exc).__name__,
            )
            raise QueueError(
                f"queue message {name!r} was corrupted and moved to dead-letter"
            ) from exc
        return Message(id=name, payload=payload)

    async def ack(self, message_id: str) -> None:
        """Move a claimed message from ``processing/`` to ``done/``."""
        self._ensure_open()
        await asyncio.to_thread(self._ack_sync, message_id)

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
        await asyncio.to_thread(self._nack_sync, message_id, requeue)

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
            os.replace(src, self._dead_letter / message_id)
        _fsync_dir(self._incoming)
        _fsync_dir(self._processing)
        _fsync_dir(self._dead_letter)

    async def close(self) -> None:
        """Mark the backend closed so subsequent operations error out.

        Directories are left in place — they are the durable queue
        state and the next process instance reuses them.
        """
        self._closed = True
