"""Verified, atomic installation of Whisper model files.

Every Septum code path that may fetch a Whisper model (setup wizard,
settings install, audio ingestion) goes through :func:`install_whisper_file`
before ``whisper.load_model`` runs, so ``load_model`` always finds a file
that already passed its SHA256 check and never downloads on its own. The
final cache path is only ever written by an atomic rename of a verified
temporary file.

Integrity rests on that verify-then-replace step alone. The ``flock`` on a
sidecar lock file only keeps workers or replicas that share the cache
volume from downloading the same model twice. ``fcntl`` makes this module
POSIX-only.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx
from septum_core.model_integrity import ModelRefusal

from ..errors import CodedHTTPError, ContentFreeError

logger = logging.getLogger(__name__)

WHISPER_MODEL_NOT_ALLOWED_CODE = "whisper_model_not_allowed"
_MODEL_NOT_ALLOWED_DETAIL = "Whisper model is not in the supported model registry"

# Approximate download size per model family, used for progress, for the
# cheap status check and as the basis of the hard size cap.
WHISPER_APPROX_BYTES = {
    "tiny": 75_600_000,
    "base": 145_300_000,
    "small": 483_600_000,
    "medium": 1_528_000_000,
    "large": 3_087_000_000,
    "turbo": 1_618_000_000,
}
_SIZE_MARGIN = 1.25
_FALLBACK_MAX_BYTES = 4_000_000_000
_CHUNK_BYTES = 1 << 20
_MODEL_FILE_MODE = 0o600
_MAX_CONTENT_LENGTH_DIGITS = 20
_READ_TIMEOUT_SECONDS = 30.0
_DEADLINE_BASE_SECONDS = 600.0
_DEADLINE_MIN_BYTES_PER_SECOND = 1_000_000
_INSTALL_IN_PROGRESS = "Model install already in progress"

_thread_locks: dict[str, threading.Lock] = {}
_thread_locks_guard = threading.Lock()
# Non-waiting installs (setup wizard, settings) share one download slot per
# process, whatever the model, so concurrent requests for different models
# cannot each pin a worker thread. Waiting installs (audio ingestion) skip
# it: their concurrency is already bounded by the ingestion semaphore and
# the per-file lock, and they must not stall behind an unrelated model.
_download_slot = threading.Lock()
# Files that passed a full SHA256 check, keyed by path, valued by the stat
# identity at verification time. A replaced or modified file no longer matches.
_verified: dict[str, tuple[int, int, int, int]] = {}


class WhisperDownloadError(ContentFreeError, RuntimeError):
    """A Whisper model download was refused or failed verification."""


class WhisperInstallBusy(ContentFreeError, RuntimeError):
    """Another thread or process is installing the same model file right now."""


class WhisperModelNotAllowed(ModelRefusal, ContentFreeError):
    """A Whisper model name is outside whisper's registry; it is never loaded as a local path."""

    code = WHISPER_MODEL_NOT_ALLOWED_CODE


@dataclass(frozen=True)
class WhisperTarget:
    """Resolved download source and cache location for one Whisper model."""

    url: str
    path: Path
    sha256: str
    approx_bytes: int
    max_bytes: int

    @property
    def deadline_seconds(self) -> float:
        """Overall wall-clock budget for one download of this model."""
        return _DEADLINE_BASE_SECONDS + self.max_bytes / _DEADLINE_MIN_BYTES_PER_SECOND


def _family(model_name: str) -> str:
    """Map a Whisper model name (``base.en``, ``large-v3-turbo``) to its size family."""
    name = model_name.removesuffix(".en")
    if "turbo" in name:
        return "turbo"
    if name.startswith("large"):
        return "large"
    return name


def whisper_cache_dir() -> Path:
    """Return the directory ``whisper.load_model`` reads by default."""
    return Path(os.getenv("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "whisper"


def resolve_whisper_target(models_map: dict, model_name: str) -> WhisperTarget:
    """Resolve a model name against whisper's registry, refusing anything unsafe.

    Raises ``WhisperDownloadError`` for an unknown model, an empty or
    non-https URL, or a URL that does not carry a SHA256 path segment.
    """
    url = models_map.get(model_name) or ""
    if not url:
        raise WhisperDownloadError("Unknown Whisper model")
    if not url.startswith("https://"):
        raise WhisperDownloadError("Model download URL must use https")
    parts = url.rsplit("/", 2)
    sha256 = parts[-2].lower() if len(parts) == 3 else ""
    if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
        raise WhisperDownloadError("Model download URL carries no SHA256 checksum")
    approx = WHISPER_APPROX_BYTES.get(_family(model_name), 0)
    max_bytes = int(approx * _SIZE_MARGIN) if approx else _FALLBACK_MAX_BYTES
    return WhisperTarget(
        url=url,
        path=whisper_cache_dir() / parts[-1],
        sha256=sha256,
        approx_bytes=approx,
        max_bytes=max_bytes,
    )


def _stat_identity(path: Path) -> tuple[int, int, int, int] | None:
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    return (st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _sha256_file(path: Path) -> str:
    """Hash a file in fixed-size chunks so multi-GB models are never read whole."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cached_identity(target: WhisperTarget) -> tuple[tuple[int, int, int, int] | None, bool]:
    """Return the cached file's stat identity and whether it was verified unchanged.

    The identity is ``None`` when there is no regular file at the path.
    """
    identity = _stat_identity(target.path)
    if identity is None or not target.path.is_file():
        return None, False
    return identity, _verified.get(str(target.path)) == identity


def whisper_file_plausible(target: WhisperTarget) -> bool:
    """Cheap readiness check (no hashing) for status endpoints.

    True for a file verified earlier in this process and unchanged since,
    or for a regular file whose size fits the model family. The install
    path always runs the full SHA256 check before relying on a file.
    """
    identity, verified = _cached_identity(target)
    if identity is None:
        return False
    if verified:
        return True
    size = identity[1]
    low = target.approx_bytes / _SIZE_MARGIN if target.approx_bytes else 1
    return low <= size <= target.max_bytes


def whisper_file_verified(target: WhisperTarget, *, remove_if_corrupt: bool) -> bool:
    """Return True only when the cached model file exists and its SHA256 matches."""
    identity, verified = _cached_identity(target)
    if identity is None:
        return False
    if verified:
        return True
    if _sha256_file(target.path) == target.sha256:
        _verified[str(target.path)] = identity
        return True
    logger.warning("Cached Whisper model failed checksum verification: %s", target.path.name)
    _verified.pop(str(target.path), None)
    if remove_if_corrupt:
        target.path.unlink(missing_ok=True)
    return False


@contextmanager
def _install_lock(target: WhisperTarget, *, wait: bool) -> Iterator[None]:
    """Serialise installs of one model file across threads and processes.

    A per-path thread lock covers this process; an exclusive ``flock`` on a
    sidecar lock file covers other workers or replicas sharing the cache
    volume. Aliases that resolve to one file (``large``/``large-v3``) share
    both locks. With ``wait=False`` a held lock raises ``WhisperInstallBusy``
    at once instead of parking the calling thread.
    """
    import fcntl

    with _thread_locks_guard:
        thread_lock = _thread_locks.setdefault(str(target.path), threading.Lock())
    if not thread_lock.acquire(blocking=wait):
        raise WhisperInstallBusy(_INSTALL_IN_PROGRESS)
    try:
        target.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = target.path.with_name(target.path.name + ".lock")
        with open(lock_path, "a+b") as lock_file:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
            except BlockingIOError:
                raise WhisperInstallBusy(_INSTALL_IN_PROGRESS) from None
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    finally:
        thread_lock.release()


def _sweep_stale_parts(target: WhisperTarget) -> None:
    """Remove temp files of this model left by writers that died mid-download.

    A live writer refreshes its temp file's mtime on every write and never
    runs past the deadline, so anything older than the deadline is orphaned.
    Must run under ``_install_lock``.
    """
    cutoff = time.time() - target.deadline_seconds
    for part in target.path.parent.glob(f".{target.path.name}.*.part"):
        try:
            if part.stat().st_mtime < cutoff:
                part.unlink(missing_ok=True)
        except FileNotFoundError:
            continue


def _download(target: WhisperTarget, progress: dict[str, int]) -> None:
    """Stream the model to a unique temp file, verify it, then rename it into place.

    The final path only ever receives a file whose SHA256 matched; the
    temp file is removed on any failure. Must run under ``_install_lock``.
    """
    fd, tmp_name = tempfile.mkstemp(
        dir=target.path.parent, prefix=f".{target.path.name}.", suffix=".part"
    )
    tmp_path = Path(tmp_name)
    deadline = time.monotonic() + target.deadline_seconds
    try:
        with os.fdopen(fd, "wb") as output, httpx.stream(
            "GET",
            target.url,
            headers={"Accept-Encoding": "identity"},
            timeout=httpx.Timeout(_READ_TIMEOUT_SECONDS),
            follow_redirects=True,
        ) as source:
            source.raise_for_status()
            hops = [*(r.url for r in source.history), source.url]
            if any(hop.scheme != "https" for hop in hops):
                raise WhisperDownloadError("Model download was redirected away from https")
            limit = target.max_bytes
            declared = source.headers.get("Content-Length")
            if declared:
                if (
                    len(declared) > _MAX_CONTENT_LENGTH_DIGITS
                    or not (declared.isascii() and declared.isdecimal())
                    or int(declared) > target.max_bytes
                ):
                    raise WhisperDownloadError("Model download exceeds the expected size")
                limit = int(declared)
                progress["total"] = limit
            digest = hashlib.sha256()
            written = 0
            # Raw wire bytes: what Content-Length counts and what the SHA256 covers.
            for buf in source.iter_raw():
                written += len(buf)
                if written > limit:
                    raise WhisperDownloadError("Model download exceeds the expected size")
                if time.monotonic() > deadline:
                    raise WhisperDownloadError("Model download timed out")
                digest.update(buf)
                output.write(buf)
                progress["downloaded"] = written
            os.fchmod(output.fileno(), _MODEL_FILE_MODE)
        if digest.hexdigest() != target.sha256:
            raise WhisperDownloadError("SHA256 checksum mismatch — please retry")
        os.replace(tmp_path, target.path)
        identity = _stat_identity(target.path)
        if identity is not None:
            _verified[str(target.path)] = identity
    finally:
        tmp_path.unlink(missing_ok=True)


def install_whisper_file(
    target: WhisperTarget,
    progress: dict[str, int] | None = None,
    *,
    wait: bool = True,
) -> None:
    """Install one model file, reusing a verified cached copy when present.

    ``wait=False`` raises ``WhisperInstallBusy`` instead of blocking while
    another installer holds this model's lock or the process-wide download
    slot. Only ``wait=False`` installs use that slot, and a verified cached
    copy never needs it.
    """
    progress = progress if progress is not None else {"downloaded": 0, "total": 0}
    with _install_lock(target, wait=wait):
        _sweep_stale_parts(target)
        if whisper_file_verified(target, remove_if_corrupt=True):
            progress["downloaded"] = progress["total"]
            return
        if wait:
            _download(target, progress)
            return
        if not _download_slot.acquire(blocking=False):
            raise WhisperInstallBusy(_INSTALL_IN_PROGRESS)
        try:
            _download(target, progress)
        finally:
            _download_slot.release()


def registry_models() -> dict:
    """Return whisper's model registry (``whisper._MODELS``)."""
    import whisper

    return getattr(whisper, "_MODELS", {})


def require_registry_model(model_name: str) -> str:
    """Return ``model_name`` if whisper's registry lists it, else raise :class:`WhisperModelNotAllowed`.

    When the whisper package cannot be imported the registry is unknown, so
    every name is refused. The message is fixed text: the name is never echoed.
    """
    try:
        registered = model_name in registry_models()
    except ImportError:
        registered = False
    if not registered:
        raise WhisperModelNotAllowed(_MODEL_NOT_ALLOWED_DETAIL)
    return model_name


async def require_registry_model_http(model_name: str) -> str:
    """Endpoint form of :func:`require_registry_model`: refuse with HTTP 400 and the refusal code.

    Used by every endpoint that saves or installs a Whisper model name; the
    registry lookup imports whisper, so it runs off the event loop.
    """
    try:
        return await asyncio.to_thread(require_registry_model, model_name)
    except WhisperModelNotAllowed as exc:
        raise CodedHTTPError(400, str(exc), code=exc.code) from None


def ensure_whisper_model(model_name: str, *, wait: bool = True) -> None:
    """Make sure a registry model is cached and verified before ``load_model`` runs.

    A name outside whisper's registry raises :class:`WhisperModelNotAllowed`.
    ``whisper.load_model`` would open it as a local checkpoint path with no
    hash check, so it is never passed on. ``wait`` is passed to
    :func:`install_whisper_file`.
    """
    models_map = registry_models()
    if model_name not in models_map:
        raise WhisperModelNotAllowed(_MODEL_NOT_ALLOWED_DETAIL)
    install_whisper_file(resolve_whisper_target(models_map, model_name), wait=wait)
