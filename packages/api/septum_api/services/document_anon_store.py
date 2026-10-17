"""Per-document anonymization map store with 3-tier caching.

Tier 1: In-memory dict (fastest, single-worker)
Tier 2: Redis (shared across workers, 24h TTL)
Tier 3: Encrypted disk (persistent across restarts, AES-256-GCM)

Maps are populated at document upload and used at chat time for
de-anonymizing LLM responses. They are never sent to the frontend,
cloud, or logs. When a document is deleted, its map is removed from
all tiers.

The memory tier is per worker, so every in-memory copy carries the
version it was loaded at and is trusted only while the persistent tier
still reports that version: with Redis configured the version lives in a
Redis key (erasure replaces it with a tombstone); without Redis it is the
identity of the encrypted disk file (inode, mtime, size), which erasure
removes. Another worker's delete or rewrite therefore invalidates every
cached copy on its next read.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
import weakref
from pathlib import Path
from typing import AsyncIterator, Dict, Optional, Tuple
from uuid import uuid4

from ..utils.crypto import decrypt, encrypt
from .anonymization_map import AnonymizationMap
from .redis_client import (
    get_redis,
    redis_configured,
    redis_delete,
    redis_get,
    redis_set,
    redis_set_if_absent,
)

logger = logging.getLogger(__name__)

_document_maps: Dict[int, AnonymizationMap] = {}
_map_versions: Dict[int, str] = {}
_map_write_locks: "weakref.WeakValueDictionary[int, asyncio.Lock]" = weakref.WeakValueDictionary()

_ANON_MAP_DIR = Path(
    os.getenv("ANON_MAP_STORAGE_DIR", "./anon_maps")
).resolve()

_REDIS_TTL = 86400  # 24 hours
# Outlives any Redis map copy a racing backfill could still write after
# the erasure, so that copy is never trusted.
_TOMBSTONE_TTL = 2 * _REDIS_TTL
_TOMBSTONE = "erased"
_LOCK_TIMEOUT_SECONDS = 60
_LOCK_WAIT_SECONDS = 60
# A temp file older than this cannot belong to a write still in flight.
_STALE_TMP_SECONDS = 300


class DocumentMapLockTimeout(TimeoutError):
    """The cross-worker map lock could not be acquired in time."""


class DocumentMapLockLost(RuntimeError):
    """The cross-worker map lock expired or was taken over mid-update."""


class DocumentMapErasureError(OSError):
    """A tier still holds the map of a document that is being erased."""


class DocumentMapUnreadableError(RuntimeError):
    """The encrypted map file exists but cannot be read or decrypted."""


class DocumentMapLockHandle:
    """Held map lock; :meth:`ensure_held` is called right before a write."""

    def __init__(self, document_id: int, redis_lock=None) -> None:
        self._document_id = document_id
        self._redis_lock = redis_lock

    async def ensure_held(self) -> None:
        """Confirm the lock is still ours and renew its full timeout.

        Raises :class:`DocumentMapLockLost` if the Redis lock expired or
        another worker holds it, so the caller aborts instead of writing a
        map someone else may be writing too. A no-op without Redis.
        """
        if self._redis_lock is None:
            return
        from redis.exceptions import LockError

        try:
            await self._redis_lock.extend(_LOCK_TIMEOUT_SECONDS, replace_ttl=True)
        except LockError as exc:
            raise DocumentMapLockLost(
                f"Lost the anonymization map lock of document_id={self._document_id}"
            ) from exc


def _map_path(document_id: int) -> Path:
    """Path to the encrypted map file for a document."""
    _ANON_MAP_DIR.mkdir(parents=True, exist_ok=True)
    return _ANON_MAP_DIR / f"{document_id}.enc"


def sweep_stale_temp_files() -> int:
    """Remove temp files a crashed atomic write left in the map directory.

    Called at startup. A temp file holds the same ciphertext as the map
    it was replacing, so an orphan would outlive that map's erasure.
    Only files older than :data:`_STALE_TMP_SECONDS` are removed, so a
    write still in flight in another worker is never disturbed. Returns
    the number of files removed; failures are logged without the path.
    """
    if not _ANON_MAP_DIR.is_dir():
        return 0
    cutoff = time.time() - _STALE_TMP_SECONDS
    removed = 0
    for tmp in _ANON_MAP_DIR.glob("*.tmp"):
        try:
            if tmp.stat().st_mtime < cutoff:
                tmp.unlink(missing_ok=True)
                removed += 1
        except OSError as exc:
            logger.error(
                "Could not remove a stale anonymization map temp file: %s",
                type(exc).__name__,
            )
    if removed:
        logger.info("Removed %d stale anonymization map temp files", removed)
    return removed


@contextlib.asynccontextmanager
async def document_map_lock(document_id: int) -> AsyncIterator[DocumentMapLockHandle]:
    """Serialise read-modify-write of a document map.

    Callers that extend a stored map after ingestion (inline chunk edits,
    label edits) hold it so two concurrent edits cannot drop each other's
    new entities. A process-local lock always applies; when Redis is
    configured a Redis lock is held as well, so separate workers are
    serialised too. Inside the lock, read the map with
    :func:`load_persisted_document_map` and call
    :meth:`DocumentMapLockHandle.ensure_held` right before writing it.

    A Redis lock found expired on release raises
    :class:`DocumentMapLockLost` — unless the body already raised, in which
    case the loss is logged and the body's error propagates unchanged.
    """
    lock_guard = _map_write_locks.get(document_id)
    if lock_guard is None:
        lock_guard = _map_write_locks[document_id] = asyncio.Lock()
    async with lock_guard:
        client = await get_redis()
        if client is None:
            yield DocumentMapLockHandle(document_id)
            return
        from redis.exceptions import LockError

        lock = client.lock(
            f"anon_map_lock:{document_id}",
            timeout=_LOCK_TIMEOUT_SECONDS,
            blocking_timeout=_LOCK_WAIT_SECONDS,
        )
        if not await lock.acquire():
            raise DocumentMapLockTimeout(
                f"Could not lock the anonymization map of document_id={document_id}"
            )
        body_failed = False
        try:
            yield DocumentMapLockHandle(document_id, lock)
        except BaseException:
            body_failed = True
            raise
        finally:
            try:
                await lock.release()
            except LockError as exc:
                logger.error(
                    "Anonymization map lock of document_id=%s expired before release",
                    document_id,
                )
                if not body_failed:
                    raise DocumentMapLockLost(
                        f"Lost the anonymization map lock of document_id={document_id}"
                    ) from exc


def _redis_key(document_id: int) -> str:
    return f"anon_map:{document_id}"


def _version_key(document_id: int) -> str:
    return f"anon_map_version:{document_id}"


def _disk_version(stat_result: os.stat_result) -> str:
    return f"{stat_result.st_ino}:{stat_result.st_mtime_ns}:{stat_result.st_size}"


async def _current_version(document_id: int) -> Optional[str]:
    """Return the persistent tier's version of the map, if it has one.

    Redis configured: the version key (``None`` when missing or Redis is
    unreachable, :data:`_TOMBSTONE` once erased). Otherwise the disk
    file's identity, ``None`` when the file does not exist.
    """
    if redis_configured():
        raw = await redis_get(_version_key(document_id))
        return raw.decode("utf-8", "replace") if raw is not None else None
    try:
        return _disk_version(os.stat(_map_path(document_id)))
    except FileNotFoundError:
        return None


def _remember(document_id: int, anon_map: AnonymizationMap, version: Optional[str]) -> None:
    """Cache ``anon_map`` in memory under ``version``.

    Without a version (unknown, or tombstoned by an erasure) nothing is
    cached and any older copy is dropped: an unversioned plaintext copy
    could never be invalidated, so it would outlive an erasure.
    """
    if version is None or version == _TOMBSTONE:
        _forget(document_id)
        return
    _document_maps[document_id] = anon_map
    _map_versions[document_id] = version


def _forget(document_id: int) -> None:
    _document_maps.pop(document_id, None)
    _map_versions.pop(document_id, None)


def _write_disk_atomically(document_id: int, data: bytes) -> os.stat_result:
    """Write the disk tier via a temp file and rename; return the file's stat.

    Readers in other workers never see a half-written file, and every
    write produces a new inode, so the disk version changes on each write.
    """
    path = _map_path(document_id)
    tmp = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            stat_result = os.fstat(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise
    return stat_result


def _serialize(anon_map: AnonymizationMap) -> bytes:
    """Serialize anonymization map fields."""
    payload = {
        "entity_map": anon_map.entity_map,
        "language": anon_map.language,
        "token_to_placeholder": anon_map.token_to_placeholder,
        "token_counter": anon_map.token_counter,
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _deserialize(document_id: int, data: bytes) -> AnonymizationMap:
    """Deserialize payload into an AnonymizationMap."""
    payload = json.loads(data.decode("utf-8"))
    return AnonymizationMap(
        document_id=document_id,
        language=payload.get("language", "en"),
        entity_map=payload.get("entity_map", {}),
        token_to_placeholder=payload.get("token_to_placeholder", {}),
        token_counter=payload.get("token_counter", {}),
    )


async def set_document_map(document_id: int, anon_map: AnonymizationMap) -> None:
    """Store the anonymization map in all three tiers.

    All tiers carry the SAME AES-256-GCM ciphertext. Redis used to hold
    plaintext JSON which exposed every original PII value to anyone with
    Redis read access (RDB/AOF backups, MITM on cleartext links,
    compromised infra). The disk-tier ciphertext is reused so an
    operator never accidentally writes raw PII to either persistence
    layer.

    The disk tier is written first and a write failure raises
    :class:`OSError` without touching the other tiers, so no caller can
    commit text that references a map which was never persisted. A failed
    Redis write drops the Redis copy so readers fall through to disk
    instead of seeing a stale map.
    """
    serialized = _serialize(anon_map)
    associated_data = str(document_id).encode("utf-8")
    encrypted_bytes = encrypt(serialized, associated_data=associated_data)

    # Tier 3: encrypted disk (durable; failure aborts the write)
    disk_stat = _write_disk_atomically(document_id, encrypted_bytes)

    # Tier 2: Redis (encrypted), then the version other workers validate
    # their memory copy against.
    map_written = await redis_set(_redis_key(document_id), encrypted_bytes, ttl=_REDIS_TTL)
    if not map_written:
        await redis_delete(_redis_key(document_id))
    if redis_configured():
        version: Optional[str] = uuid4().hex
        if not (map_written and await redis_set(_version_key(document_id), version.encode("ascii"))):
            await redis_delete(_version_key(document_id))
            version = None
    else:
        version = _disk_version(disk_stat)

    # Tier 1: memory (plaintext, in-process only)
    _remember(document_id, anon_map, version)


async def get_document_map(document_id: int) -> AnonymizationMap | None:
    """Return the anonymization map, checking tiers in order.

    Tier 1 (memory) → Tier 2 (Redis) → Tier 3 (encrypted disk). The memory
    copy is returned only while the persistent tier still reports the
    version it was cached at; an erased map (tombstone, or no disk file
    without Redis) returns ``None`` and drops the memory copy. On a miss
    the map is loaded and higher tiers are backfilled.
    """
    version = await _current_version(document_id)
    if version == _TOMBSTONE:
        _forget(document_id)
        return None
    cached = _document_maps.get(document_id)
    if cached is not None and version is not None and _map_versions.get(document_id) == version:
        return cached
    _forget(document_id)
    if version is None and not redis_configured():
        return None
    anon_map = await _load_persisted(document_id)
    if anon_map is None:
        return None
    if version is None:
        # Legacy map or lost version key: claim one unless another worker
        # (or an erasure tombstone) got there first.
        candidate = uuid4().hex
        if await redis_set_if_absent(_version_key(document_id), candidate.encode("ascii")):
            version = candidate
        elif await _current_version(document_id) == _TOMBSTONE:
            await redis_delete(_redis_key(document_id))
            return None
    _remember(document_id, anon_map, version)
    return anon_map


async def load_persisted_document_map(document_id: int) -> AnonymizationMap | None:
    """Return the map as last written to disk, bypassing memory and Redis.

    The encrypted disk file is the authoritative tier: every write lands
    there first, while Redis can lag (a failed or expired write) and the
    memory tier is per process. A read-modify-write under
    :func:`document_map_lock` therefore reads disk, so it sees entities
    another worker added. The memory tier is refreshed with the result.
    Returns ``None`` when no disk copy can be read (a memory copy without
    its disk file is never authoritative) or the map was tombstoned by an
    erasure.
    """
    version = await _current_version(document_id)
    if version == _TOMBSTONE:
        _forget(document_id)
        return None
    try:
        loaded = _load_from_disk(document_id)
    except DocumentMapUnreadableError:
        logger.error(
            "Anonymization map of document_id=%s exists but is unreadable; not overwriting it",
            document_id,
        )
        raise
    if loaded is None:
        return None
    anon_map = loaded[1]
    _remember(document_id, anon_map, version)
    return anon_map


def _load_from_disk(document_id: int) -> Optional[Tuple[bytes, AnonymizationMap]]:
    """Return ``(ciphertext, map)`` from the disk tier, or ``None`` when no file exists.

    A file that exists but cannot be read or decrypted raises
    :class:`DocumentMapUnreadableError`. Callers must not treat that as
    absent: a fresh map would then overwrite the stored one.
    """
    path = _map_path(document_id)
    try:
        encrypted_bytes = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise DocumentMapUnreadableError(
            f"Could not read the anonymization map of document_id={document_id}"
        ) from exc
    try:
        plaintext = decrypt(
            encrypted_bytes, associated_data=str(document_id).encode("utf-8")
        )
        return encrypted_bytes, _deserialize(document_id, plaintext)
    except Exception as exc:
        raise DocumentMapUnreadableError(
            f"Could not decrypt the anonymization map of document_id={document_id}"
        ) from exc


async def _load_persisted(document_id: int) -> AnonymizationMap | None:
    """Load from Redis, then encrypted disk, backfilling Redis."""
    associated_data = str(document_id).encode("utf-8")

    # Tier 2: Redis (encrypted)
    redis_data = await redis_get(_redis_key(document_id))
    if redis_data is not None:
        try:
            plaintext = decrypt(redis_data, associated_data=associated_data)
            return _deserialize(document_id, plaintext)
        except Exception as exc:
            logger.warning(
                "Could not decrypt Redis anon map for document_id=%s "
                "(falling through to disk): %s",
                document_id,
                type(exc).__name__,
            )
            await redis_delete(_redis_key(document_id))

    # Tier 3: encrypted disk
    try:
        loaded = _load_from_disk(document_id)
        if loaded is None:
            logger.debug("No anonymization map file for document_id=%s", document_id)
            return None
        encrypted_bytes, anon_map = loaded

        # Backfill Redis with ciphertext, not plaintext, so the bridge
        # tier never holds raw originals. The caller backfills memory.
        if await redis_set(_redis_key(document_id), encrypted_bytes, ttl=_REDIS_TTL):
            # An erasure that ran while the file was read must not leave
            # this copy behind.
            if await _current_version(document_id) == _TOMBSTONE:
                await redis_delete(_redis_key(document_id))
                return None

        return anon_map
    except Exception as e:
        logger.warning(
            "Could not load anonymization map for document_id=%s (deanonymization will be skipped): %s",
            document_id,
            type(e).__name__,
        )
        return None


async def pop_document_map(document_id: int) -> None:
    """Remove the map from all tiers.

    Erasure must be verifiable: if the Redis copy (when Redis is
    configured) or the disk file cannot be removed, the failure is logged
    with the document id only and :class:`DocumentMapErasureError` is
    raised, so the caller cannot report the document as deleted. A
    configured but unreachable Redis counts as a failed tier, as does an
    orphaned temp file of an interrupted disk write. With Redis,
    the version key is replaced by a tombstone so other workers drop their
    memory copy and ignore any Redis copy a racing backfill writes back.
    """
    # Tier 1: memory
    _forget(document_id)

    failed_tiers: list[str] = []
    disk_error: OSError | None = None

    # Tier 2: Redis
    client = await get_redis()
    if client is None:
        if redis_configured():
            failed_tiers.append("redis")
    else:
        tombstoned = await redis_set(
            _version_key(document_id), _TOMBSTONE.encode("ascii"), ttl=_TOMBSTONE_TTL
        )
        if not (tombstoned and await redis_delete(_redis_key(document_id))):
            failed_tiers.append("redis")

    # Tier 3: disk, including temp files of interrupted atomic writes
    path = _map_path(document_id)
    for target in (path, *path.parent.glob(f"{path.name}.*.tmp")):
        try:
            target.unlink(missing_ok=True)
        except OSError as exc:
            if "disk" not in failed_tiers:
                failed_tiers.append("disk")
            disk_error = exc

    if failed_tiers:
        logger.error(
            "Could not erase the anonymization map of document_id=%s tiers=%s",
            document_id,
            failed_tiers,
        )
        raise DocumentMapErasureError(
            f"Anonymization map of document_id={document_id} was not erased "
            f"from: {', '.join(failed_tiers)}"
        ) from disk_error
