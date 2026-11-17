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
from .index_file_io import document_ids_in, parse_document_id
from .redis_client import (
    get_redis,
    redis_configured,
    redis_delete,
    redis_get,
    redis_set,
    redis_set_if_absent,
    tracked,
)

logger = logging.getLogger(__name__)

_document_maps: Dict[int, AnonymizationMap] = {}
_map_versions: Dict[int, str] = {}
_map_write_locks: "weakref.WeakValueDictionary[int, asyncio.Lock]" = weakref.WeakValueDictionary()

_ANON_MAP_DIR = Path(
    os.getenv("ANON_MAP_STORAGE_DIR", "./anon_maps")
).resolve()

_REDIS_MAP_PREFIX = "anon_map:"
_REDIS_TTL = 86400  # 24 hours
# Outlives the Redis map copy's TTL; once the tombstone expires the version
# key is absent, which no cached memory copy matches either.
_TOMBSTONE_TTL = 2 * _REDIS_TTL
_TOMBSTONE = "erased"
# The Redis map copy is stored as ``_COPY_PREFIX + version + b"|" + ciphertext``
# and trusted only under that version (see :func:`_load_persisted`); a copy
# without the prefix (written before) counts as absent.
_COPY_PREFIX = b"v1|"
_LOCK_TIMEOUT_SECONDS = 60
_LOCK_WAIT_SECONDS = 60
# Startup sweeps and the failure handler wait on one document lock at a
# time; a stuck lock must not hold them for the full request-path wait,
# and a timeout is retried at the next startup.
SWEEP_LOCK_WAIT_SECONDS = 3
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


class DocumentMapCacheError(RuntimeError):
    """A configured Redis may still hold an older map that a write could neither replace nor drop."""


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
            await tracked(self._redis_lock.extend(_LOCK_TIMEOUT_SECONDS, replace_ttl=True))
        except LockError as exc:
            raise DocumentMapLockLost(
                f"Lost the anonymization map lock of document_id={self._document_id}"
            ) from exc


def _map_path(document_id: int) -> Path:
    """Path to the encrypted map file for a document."""
    _ANON_MAP_DIR.mkdir(parents=True, exist_ok=True)
    return _ANON_MAP_DIR / f"{document_id}.enc"


def disk_map_document_ids() -> set[int]:
    """Return the ids of every document that has an encrypted map file on disk."""
    return document_ids_in(_ANON_MAP_DIR, "", ".enc")


async def redis_map_document_ids() -> set[int]:
    """Return the ids of every document whose map is cached in Redis.

    Empty without Redis. A configured Redis that cannot be reached raises
    :class:`DocumentMapErasureError`, so a caller hunting leftovers never
    mistakes an unreadable tier for an empty one.
    """
    if not redis_configured():
        return set()
    client = get_redis()
    if client is None:
        raise DocumentMapErasureError("Redis is configured but unavailable")
    prefix = _REDIS_MAP_PREFIX.encode("ascii")
    ids: set[int] = set()
    async for key in client.scan_iter(match=prefix + b"*"):
        document_id = parse_document_id(key[len(prefix):].decode("ascii", "replace"))
        if document_id is not None:
            ids.add(document_id)
    return ids


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
async def document_map_lock(
    document_id: int, *, wait_seconds: float = _LOCK_WAIT_SECONDS
) -> AsyncIterator[DocumentMapLockHandle]:
    """Serialise read-modify-write of a document map.

    Callers that extend a stored map after ingestion (inline chunk edits,
    label edits) hold it so two concurrent edits cannot drop each other's
    new entities. A process-local lock always applies; when Redis is
    configured a Redis lock is held as well, so separate workers are
    serialised too; without Redis it serialises one worker only, so a
    multi-worker deployment needs Redis. Inside the lock, read the map with
    :func:`load_persisted_document_map` and call
    :meth:`DocumentMapLockHandle.ensure_held` right before writing it.

    ``wait_seconds`` bounds the wait for another worker's Redis lock
    before :class:`DocumentMapLockTimeout` is raised. A configured Redis
    that is unavailable (unreachable, or a URL no client can be made from)
    raises :class:`DocumentMapLockTimeout` at once rather than serialising
    this worker only.

    A Redis lock found expired on release raises
    :class:`DocumentMapLockLost` — unless the body already raised, in which
    case the loss is logged and the body's error propagates unchanged. A
    release that cannot reach Redis is logged (document id and error class)
    and never raises: the body's outcome stands, and the lock expires on
    its own after ``_LOCK_TIMEOUT_SECONDS``.
    """
    lock_guard = _map_write_locks.get(document_id)
    if lock_guard is None:
        lock_guard = _map_write_locks[document_id] = asyncio.Lock()
    async with lock_guard:
        client = get_redis()
        if client is None:
            if redis_configured():
                raise DocumentMapLockTimeout(
                    "Redis is configured but unavailable; could not lock the "
                    f"anonymization map of document_id={document_id}"
                )
            yield DocumentMapLockHandle(document_id)
            return
        from redis.exceptions import LockError

        lock = client.lock(
            f"anon_map_lock:{document_id}",
            timeout=_LOCK_TIMEOUT_SECONDS,
            blocking_timeout=wait_seconds,
        )
        try:
            acquired = await tracked(lock.acquire())
        except Exception as exc:
            raise DocumentMapLockTimeout(
                f"Could not reach Redis to lock the anonymization map of document_id={document_id}"
            ) from exc
        if not acquired:
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
                await tracked(lock.release())
            except LockError as exc:
                logger.error(
                    "Anonymization map lock of document_id=%s expired before release",
                    document_id,
                )
                if not body_failed:
                    raise DocumentMapLockLost(
                        f"Lost the anonymization map lock of document_id={document_id}"
                    ) from exc
            except Exception as exc:
                logger.error(
                    "Could not release the anonymization map lock of document_id=%s: %s",
                    document_id,
                    type(exc).__name__,
                )


def _redis_key(document_id: int) -> str:
    return f"{_REDIS_MAP_PREFIX}{document_id}"


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


def _replace_atomically(path: Path, data: bytes) -> os.stat_result:
    """Replace ``path`` with ``data`` via a temp file and rename; return the new file's stat.

    Readers in other workers never see a half-written file, and every write
    produces a new inode, so the disk version changes on each write.
    """
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


async def _drop_redis_copy(document_id: int) -> bool:
    """Delete the Redis map copy and its version key in one command; whether it landed."""
    return await redis_delete(_redis_key(document_id), _version_key(document_id))


async def set_document_map(document_id: int, anon_map: AnonymizationMap) -> None:
    """Store the anonymization map in all three tiers.

    All tiers carry the SAME AES-256-GCM ciphertext. Redis used to hold
    plaintext JSON which exposed every original PII value to anyone with
    Redis read access (RDB/AOF backups, MITM on cleartext links,
    compromised infra). The disk-tier ciphertext is reused so an
    operator never accidentally writes raw PII to either persistence
    layer.

    With Redis configured, the Redis copy and its version key are dropped
    before the disk write and again right after it, then written anew, so
    Redis holds the current map or none: a write cut short leaves no older
    map under a version warm workers would trust, and the second drop
    removes a version another worker claimed for the old file meanwhile.
    Any failure drops the Redis keys once more before it propagates. If
    neither the second drop, the writes nor a last drop landed,
    :class:`DocumentMapCacheError` is raised and the caller rolls back.

    The disk tier is the authoritative one: a disk write failure raises
    :class:`OSError`, so no caller can commit text that references a map
    which was never persisted.
    """
    serialized = _serialize(anon_map)
    associated_data = str(document_id).encode("utf-8")
    encrypted_bytes = encrypt(serialized, associated_data=associated_data)
    with_redis = redis_configured()
    if with_redis:
        await _drop_redis_copy(document_id)
    try:
        disk_stat = _replace_atomically(_map_path(document_id), encrypted_bytes)
        if with_redis:
            dropped_after_write = await _drop_redis_copy(document_id)
            version: Optional[str] = uuid4().hex
            written = await redis_set(
                _redis_key(document_id), _versioned_copy(version, encrypted_bytes), ttl=_REDIS_TTL
            ) and await redis_set(_version_key(document_id), version.encode("ascii"))
            if not written:
                version = None
                if not (await _drop_redis_copy(document_id) or dropped_after_write):
                    logger.error(
                        "The Redis copy of the anonymization map of document_id=%s "
                        "could be neither replaced nor dropped",
                        document_id,
                    )
                    raise DocumentMapCacheError(
                        f"Redis may still hold an older map of document_id={document_id}"
                    )
        else:
            version = _disk_version(disk_stat)
    except BaseException:
        if with_redis:
            await asyncio.shield(_drop_redis_copy(document_id))
        raise

    # Tier 1: memory (plaintext, in-process only)
    _remember(document_id, anon_map, version)


def _is_erased(version: Optional[str]) -> bool:
    """Whether ``version`` marks the map erased: a tombstone, or no disk file without Redis."""
    return version == _TOMBSTONE or (version is None and not redis_configured())


async def get_document_map(document_id: int) -> AnonymizationMap | None:
    """Return the anonymization map, checking tiers in order.

    Tier 1 (memory) → Tier 2 (Redis) → Tier 3 (encrypted disk). The memory
    copy is returned only while the persistent tier still reports the
    version it was cached at; an erased map returns ``None`` and drops the
    memory copy. On a miss the map is loaded (:func:`_load_persisted`) and
    the version read again: an erasure seen then returns ``None``, any
    other change returns the map uncached. A map without a version claims
    one (expiring with the map copy's TTL) before it is loaded, so the
    claim only covers a file read after it.
    """
    version = await _current_version(document_id)
    if _is_erased(version):
        _forget(document_id)
        return None
    cached = _document_maps.get(document_id)
    if cached is not None and version is not None and _map_versions.get(document_id) == version:
        return cached
    _forget(document_id)
    if version is None:
        candidate = uuid4().hex
        if await redis_set_if_absent(
            _version_key(document_id), candidate.encode("ascii"), ttl=_REDIS_TTL
        ):
            version = candidate
        else:
            version = await _current_version(document_id)
            if _is_erased(version):
                return None
    anon_map = await _load_persisted(document_id, version)
    if anon_map is None:
        return None
    current = await _current_version(document_id)
    if _is_erased(current):
        _forget(document_id)
        return None
    if current is not None and current == version:
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


def _versioned_copy(version: str, encrypted_bytes: bytes) -> bytes:
    """The Redis map copy: the ciphertext tagged with the version it is written under."""
    return _COPY_PREFIX + version.encode("ascii") + b"|" + encrypted_bytes


def _copy_ciphertext(stored: bytes, version: Optional[str]) -> Optional[bytes]:
    """The ciphertext of a Redis map copy written under ``version``, or ``None`` for any other copy."""
    if version is None or not stored.startswith(_COPY_PREFIX):
        return None
    tag, separator, ciphertext = stored[len(_COPY_PREFIX):].partition(b"|")
    if not separator or tag != version.encode("ascii"):
        return None
    return ciphertext


async def _load_persisted(document_id: int, version: Optional[str]) -> AnonymizationMap | None:
    """Load from Redis, then encrypted disk; never write Redis.

    Only :func:`set_document_map` writes the Redis copy: a read that copied
    disk into Redis could land after a newer write and leave the older map
    under the newer version. The Redis copy is used only when it was written
    under ``version``; any other copy is skipped for the disk. The caller
    re-reads the version afterwards to catch an erasure that raced the read.
    """
    associated_data = str(document_id).encode("utf-8")

    # Tier 2: Redis (encrypted)
    stored = await redis_get(_redis_key(document_id))
    redis_data = _copy_ciphertext(stored, version) if stored is not None else None
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
        return loaded[1]
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
    memory copy.
    """
    # Tier 1: memory
    _forget(document_id)

    failed_tiers: list[str] = []
    disk_error: OSError | None = None

    # Tier 2: Redis
    client = get_redis()
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


_ERASURE_EPOCH_KEY = "erasure_epoch"
_ERASURE_EPOCH_FILE = "erasure.epoch"


def _erasure_epoch_path() -> Path:
    """The disk marker of the erasure epoch, next to the map files."""
    _ANON_MAP_DIR.mkdir(parents=True, exist_ok=True)
    return _ANON_MAP_DIR / _ERASURE_EPOCH_FILE


def _disk_epoch(path: Path) -> str:
    """The marker's identity and its random content, read from one open file.

    The content (the 32 hex digits :func:`bump_erasure_epoch` writes) keeps
    two markers apart even when a reused inode gets the same timestamp and
    size.
    """
    fd = os.open(path, os.O_RDONLY)
    try:
        identity = _disk_version(os.fstat(fd))
        content = os.read(fd, 32)
    finally:
        os.close(fd)
    return f"{identity}:{content.decode('ascii', 'replace')}"


async def read_erasure_epoch() -> Optional[str]:
    """The erasure epoch every worker sees, or ``None`` when it cannot be read.

    Kept like a map version: with Redis configured, a Redis key (claimed
    with a fresh value when missing); otherwise the identity and content of
    a marker file in the map directory (created when missing). Every document or
    user erasure replaces it (:func:`bump_erasure_epoch`), so an in-memory
    copy of derived content tagged with an older epoch is stale in every
    worker. ``None`` (Redis unreachable, a disk error) means no such copy
    may be trusted or kept.
    """
    if redis_configured():
        raw = await redis_get(_ERASURE_EPOCH_KEY)
        if raw is None:
            candidate = uuid4().hex
            if await redis_set_if_absent(_ERASURE_EPOCH_KEY, candidate.encode("ascii")):
                return candidate
            raw = await redis_get(_ERASURE_EPOCH_KEY)
        return raw.decode("utf-8", "replace") if raw is not None else None
    try:
        path = _erasure_epoch_path()
        try:
            return _disk_epoch(path)
        except FileNotFoundError:
            with contextlib.suppress(FileExistsError):
                os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
            return _disk_epoch(path)
    except OSError as exc:
        logger.error("Could not read the erasure epoch: %s", type(exc).__name__)
        return None


async def bump_erasure_epoch() -> None:
    """Replace the erasure epoch in Redis or on disk, so every worker drops what it derived before.

    A failure is logged by error class only and raises
    :class:`DocumentMapErasureError`, so the erasure that asked for it is
    not reported done.
    """
    epoch = uuid4().hex.encode("ascii")
    if redis_configured():
        if await redis_set(_ERASURE_EPOCH_KEY, epoch):
            return
        logger.error("Could not replace the erasure epoch in Redis")
        raise DocumentMapErasureError("The erasure epoch was not replaced in: redis")
    try:
        _replace_atomically(_erasure_epoch_path(), epoch)
    except OSError as exc:
        logger.error("Could not replace the erasure epoch on disk: %s", type(exc).__name__)
        raise DocumentMapErasureError("The erasure epoch was not replaced on: disk") from exc
