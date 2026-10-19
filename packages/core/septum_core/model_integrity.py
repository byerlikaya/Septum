"""Verify model files against their pins before anything loads them.

A model directory must hold exactly the pinned file set: a missing file, a
hash mismatch, or any extra file or symlink is a refusal, so nothing beyond
the pin (a planted tokenizer, a pickle weight) can reach a loader. A
verified file is remembered by its stat identity so the same process does
not re-hash it.

Loaders get a model directory only through :func:`ensure_model_snapshot`
or :func:`ensure_pinned_snapshot`; downloads go through ``huggingface_hub``
(the ``[transformers]`` extra), never a network import in this module.

User-pinned models (``org/name@<commit>`` outside the manifest) are trusted
on first use, which needs a connection: each downloaded file is checked
against the Hub's own metadata for that commit (the LFS SHA256 for LFS
files, the git blob SHA-1 for the rest) before its SHA256 is recorded in
:func:`user_pins_path`, outside the model cache.
"""

from __future__ import annotations

import errno
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from types import MappingProxyType

from .model_pins import MODEL_PINS, ModelPin

logger = logging.getLogger(__name__)

USER_PINS_FILENAME = "septum-user-pins.json"
USER_PINS_PATH_ENV = "SEPTUM_USER_PINS_PATH"

_DEFAULT_CONFIG_PATH = "/app/data/config.json"
_USER_PIN_PATTERNS = (
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "added_tokens.json",
    "vocab.txt",
    "vocab.json",
    "merges.txt",
    "*.model",
    "*.safetensors",
    "model.safetensors.index.json",
)
_OFFLINE_FIRST_USE = (
    "user-pinned model has no recorded hashes; its first use needs a connection"
)
_OFFLINE_MISSING = (
    "pinned files are not in the local model cache and offline mode is on"
)
_USER_PINS_CORRUPT = "user-pins file is unreadable or is not a JSON object"
_SPEC_RE = re.compile(r"^(?P<repo>[\w.-]+/[\w.-]+)(?:@(?P<rev>[0-9a-f]{40}))?$")
_CHUNK_BYTES = 1 << 20
_HTTP_CLIENT_MODULES = frozenset({"httpx", "httpcore"})

_StatIdentity = tuple[int, int, int, int, int]

_verified: dict[str, tuple[_StatIdentity, str]] = {}
_verified_lock = threading.Lock()


class ModelRefusal(Exception):
    """Base of every refused model load: a stable ``code`` and a Septum-authored message."""

    code = "model_refused"


class ModelIntegrityError(ModelRefusal):
    """A model directory does not match its pin; the model must not be loaded.

    The message names the model id and, where relevant, the file's path
    inside the model repository; it never carries a host filesystem path.
    """

    code = "model_integrity_failed"

    def __init__(self, model_id: str, reason: str) -> None:
        self.model_id = model_id
        self.reason = reason
        super().__init__(f"Integrity check failed for model {model_id!r}: {reason}")

    def __reduce__(self):
        return type(self), (self.model_id, self.reason)


class UnpinnedModelError(ModelRefusal, ValueError):
    """A model spec is neither a manifest model nor a valid ``org/name@<40-hex commit>``.

    The message is fixed text; the submitted spec is kept on ``spec`` only.
    """

    code = "ner_model_unpinned"

    def __init__(self, spec: str) -> None:
        self.spec = spec
        super().__init__(
            "Model is not pinned; use a built-in model or org/name@<40-hex commit sha>"
        )

    def __reduce__(self):
        return type(self), (self.spec,)


def _identity(st: os.stat_result) -> _StatIdentity:
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _hash_file(
    path: Path, git_blob: bool = False
) -> tuple[str, _StatIdentity | None, str | None]:
    """Hash ``path`` in one read.

    Returns the SHA256, the stat identity (``None`` if the file changed while
    it was read) and, with ``git_blob``, the git blob SHA-1 of the content.
    """
    digest = hashlib.sha256()
    git = None
    with path.open("rb") as fh:
        before = _identity(os.fstat(fh.fileno()))
        if git_blob:
            git = hashlib.sha1(f"blob {before[2]}\0".encode(), usedforsecurity=False)
        for block in iter(lambda: fh.read(_CHUNK_BYTES), b""):
            digest.update(block)
            if git is not None:
                git.update(block)
        after = _identity(os.fstat(fh.fileno()))
    return (
        digest.hexdigest(),
        before if before == after else None,
        git.hexdigest() if git is not None else None,
    )


def _remember(path: Path, identity: _StatIdentity, digest: str) -> None:
    with _verified_lock:
        _verified[str(path)] = (identity, digest)


def _relative_path(model_id: str, rel: str) -> PurePosixPath:
    path = PurePosixPath(rel)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ModelIntegrityError(model_id, f"pinned path {rel!r} is not repo-relative")
    return path


def _entries(root: Path) -> Iterator[tuple[str, Path]]:
    """Yield ``(repo-relative path, path)`` for every non-directory entry, symlinks included."""
    stack = [root]
    while stack:
        with os.scandir(stack.pop()) as it:
            for entry in it:
                path = Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    stack.append(path)
                else:
                    yield path.relative_to(root).as_posix(), path


def _verify_one(model_id: str, path: Path, rel: str, expected: str) -> None:
    if not path.is_file():
        raise ModelIntegrityError(model_id, f"pinned file {rel!r} is missing")
    current = _identity(path.stat())
    key = str(path)
    with _verified_lock:
        cached = _verified.get(key)
    if cached == (current, expected):
        return
    actual, identity, _ = _hash_file(path)
    if actual != expected or identity is None:
        with _verified_lock:
            _verified.pop(key, None)
        reason = (
            f"file {rel!r} changed while it was verified"
            if actual == expected
            else f"file {rel!r} does not match its pinned hash"
        )
        raise ModelIntegrityError(model_id, reason)
    _remember(path, identity, actual)


def verify_files(root: Path, pin: ModelPin) -> None:
    """Check that ``root`` holds exactly the files of ``pin``, each with its pinned SHA256.

    Raises :class:`ModelIntegrityError` when a pinned file is missing, its
    hash differs or it changes while being hashed, when a pinned path is not
    repo-relative, or when any file or symlink not in the pin is present
    under ``root``. Safe to call from several threads; a file already
    verified in this process with an unchanged stat identity is not hashed
    again.
    """
    if not root.is_dir():
        raise ModelIntegrityError(pin.repo_id, "model directory is missing")
    unexpected = sorted(rel for rel, _ in _entries(root) if rel not in pin.files)
    if unexpected:
        raise ModelIntegrityError(
            pin.repo_id, f"unpinned file {unexpected[0]!r} is present"
        )
    for rel, expected in sorted(pin.files.items()):
        path = root.joinpath(*_relative_path(pin.repo_id, rel).parts)
        _verify_one(pin.repo_id, path, rel, expected)


def require_pin(repo_id: str) -> ModelPin:
    """Return the manifest pin of ``repo_id``; raise :class:`ModelIntegrityError` if it has none."""
    pin = MODEL_PINS.get(repo_id)
    if pin is None:
        raise ModelIntegrityError(repo_id, "model has no pin")
    return pin


def parse_model_spec(spec: str) -> ModelPin | tuple[str, str]:
    """Resolve ``spec`` to its manifest pin, or to ``(repo_id, revision)`` if user-pinned.

    A bare id resolves only through the manifest; ``id@sha`` resolves to the
    manifest pin when the sha is the pinned revision, otherwise it is
    user-pinned. Raises :class:`UnpinnedModelError` for anything else,
    including an id the Hub would reject as malformed.
    """
    from huggingface_hub.errors import HFValidationError
    from huggingface_hub.utils import validate_repo_id

    match = _SPEC_RE.match(spec.strip())
    if match is None:
        raise UnpinnedModelError(spec)
    repo_id, revision = match["repo"], match["rev"]
    try:
        validate_repo_id(repo_id)
    except HFValidationError:
        raise UnpinnedModelError(spec) from None
    pin = MODEL_PINS.get(repo_id)
    if pin is not None and revision in (None, pin.revision):
        return pin
    if revision is None:
        raise UnpinnedModelError(spec)
    return repo_id, revision


def split_model_specs(overrides: dict[str, str | None] | None) -> list[str]:
    """Return the non-empty, stripped comma-separated model ids held in ``overrides``' values."""
    return [
        spec
        for value in (overrides or {}).values()
        for spec in (part.strip() for part in (value or "").split(","))
        if spec
    ]


def user_pins_path() -> Path:
    """Return where user-pinned model hashes are recorded.

    ``SEPTUM_USER_PINS_PATH`` when set; otherwise ``septum-user-pins.json``
    next to the app's ``config.json`` (``SEPTUM_CONFIG_PATH``, default
    ``/app/data``), so the record never shares a volume with the model files.
    """
    explicit = os.getenv(USER_PINS_PATH_ENV)
    if explicit:
        return Path(explicit)
    config = Path(os.getenv("SEPTUM_CONFIG_PATH", _DEFAULT_CONFIG_PATH))
    return config.parent / USER_PINS_FILENAME


def _default_cache_dir() -> Path:
    from huggingface_hub import constants

    return Path(constants.HF_HUB_CACHE)


def _is_offline() -> bool:
    from huggingface_hub import constants

    return constants.is_offline_mode()


def _repo_dir(cache_dir: Path, repo_id: str) -> Path:
    from huggingface_hub.file_download import repo_folder_name

    return cache_dir / repo_folder_name(repo_id=repo_id, repo_type="model")


def _snapshot_dir(cache_dir: Path, repo_id: str, revision: str) -> Path:
    return _repo_dir(cache_dir, repo_id) / "snapshots" / revision


def _file_lock(path: Path):
    from filelock import FileLock

    path.parent.mkdir(parents=True, exist_ok=True)
    return FileLock(str(path))


def _repo_lock(cache_dir: Path, repo_id: str):
    return _file_lock(
        cache_dir / "septum-locks" / (repo_id.replace("/", "--") + ".lock")
    )


def _is_hub_failure(exc: BaseException) -> bool:
    """Tell a Hub or connection failure from a local error (disk, permissions, a bug).

    Counts the Hub's own errors, the ``ConnectionError``/``TimeoutError``
    families and the HTTP client's exceptions; the latter are recognised by
    module name, so this module never imports a network library. Other
    ``OSError``s propagate unchanged (read-only ones are coded by the caller).
    """
    from huggingface_hub.errors import (
        HfHubHTTPError,
        LocalEntryNotFoundError,
        OfflineModeIsEnabled,
    )

    root = type(exc).__module__.split(".", 1)[0]
    return (
        isinstance(
            exc,
            (
                LocalEntryNotFoundError,
                OfflineModeIsEnabled,
                HfHubHTTPError,
                ConnectionError,
                TimeoutError,
            ),
        )
        or root in _HTTP_CLIENT_MODULES
    )


def _download(
    repo_id: str,
    revision: str,
    patterns: list[str],
    cache_dir: Path,
    force: bool = False,
) -> Path:
    """Fetch ``patterns`` at ``revision`` into the HF cache; offline reads it only.

    ``force`` re-downloads every file after a purge: the repo's
    ``blobs/<etag>`` entry and the cache-wide shared blob it may link to
    survive :func:`_purge`, and without it the Hub would re-link that same
    content instead of fetching it.
    """
    from huggingface_hub import snapshot_download

    offline = _is_offline()
    try:
        return Path(
            snapshot_download(
                repo_id,
                revision=revision,
                allow_patterns=patterns,
                cache_dir=cache_dir,
                local_files_only=offline,
                force_download=force and not offline,
            )
        )
    except Exception as exc:
        if not _is_hub_failure(exc):
            raise
        reason = (
            _OFFLINE_MISSING
            if offline
            else f"pinned revision could not be fetched ({type(exc).__name__})"
        )
        raise ModelIntegrityError(repo_id, reason) from exc


def _purge(snapshot: Path, cache_dir: Path, repo_id: str) -> None:
    """Delete one revision's snapshot and the cache blobs only it points to.

    Other revisions and every blob one of them still links to are left in
    place. Refuses (coded) when the repository, ``snapshots``, ``blobs`` or
    the snapshot itself is a symlink, or resolves outside ``cache_dir``.
    A blob held in the cache-wide shared store is never deleted here; the
    refetch that follows a purge is forced instead (see :func:`_download`).
    """
    snapshots = snapshot.parent
    repo_dir = snapshots.parent
    blobs_dir = repo_dir / "blobs"
    if any(p.is_symlink() for p in (repo_dir, snapshots, blobs_dir, snapshot)):
        raise ModelIntegrityError(repo_id, "model cache layout contains a symlink")
    if not snapshot.is_dir():
        return
    root = cache_dir.resolve()
    blobs = blobs_dir.resolve()
    if not snapshot.resolve().is_relative_to(root) or not blobs.is_relative_to(root):
        raise ModelIntegrityError(
            repo_id, "model cache layout points outside the cache"
        )
    kept = {
        path.resolve()
        for other in snapshots.iterdir()
        if other != snapshot and other.is_dir() and not other.is_symlink()
        for _, path in _entries(other)
        if path.is_symlink()
    }
    for _, path in _entries(snapshot):
        if path.is_symlink():
            target = path.resolve()
            if target.parent == blobs and target not in kept and target.is_file():
                target.unlink()
    shutil.rmtree(snapshot)


def _local_verified(pin: ModelPin, cache_dir: Path) -> Path | None:
    """Return the cached snapshot of ``pin`` if it verifies, else ``None``."""
    snapshot = _snapshot_dir(cache_dir, pin.repo_id, pin.revision)
    try:
        verify_files(snapshot, pin)
    except ModelIntegrityError:
        return None
    return snapshot


def _warn_refetch(pin: ModelPin, exc: ModelIntegrityError) -> None:
    logger.warning(
        "Model %s failed verification (%s); purging and fetching once more",
        pin.repo_id,
        exc.reason,
    )


def _refetch(
    repo_id: str, revision: str, snapshot: Path, cache_dir: Path, patterns: list[str]
) -> Path:
    """Purge one revision's snapshot and fetch ``patterns`` again, forced past every cached blob.

    Every purge-then-fetch goes through here, so the forced download cannot
    be dropped from a heal or a first use.
    """
    _purge(snapshot, cache_dir, repo_id)
    return _download(repo_id, revision, patterns, cache_dir, force=True)


def _heal(pin: ModelPin, cache_dir: Path, snapshot: Path, exc: ModelIntegrityError) -> Path:
    """Purge and refetch a snapshot that failed verification; the refetch must verify."""
    _warn_refetch(pin, exc)
    healed = _refetch(
        pin.repo_id, pin.revision, snapshot, cache_dir, sorted(pin.files)
    )
    verify_files(healed, pin)
    return healed


def _fetch_verified(pin: ModelPin, cache_dir: Path) -> tuple[Path, bool]:
    """Return a verified snapshot and whether it had to be fetched.

    A cached snapshot that fails verification is purged and fetched once;
    a fresh download that fails is purged and fetched once more. A fetch
    after a purge is forced past every cached blob. Offline, nothing is
    fetched or purged.
    """
    snapshot = _snapshot_dir(cache_dir, pin.repo_id, pin.revision)
    try:
        verify_files(snapshot, pin)
        return snapshot, False
    except ModelIntegrityError as exc:
        if _is_offline():
            if snapshot.is_dir():
                raise
            raise ModelIntegrityError(pin.repo_id, _OFFLINE_MISSING) from None
        if snapshot.is_dir():
            return _heal(pin, cache_dir, snapshot, exc), True
    fetched = _download(pin.repo_id, pin.revision, sorted(pin.files), cache_dir)
    try:
        verify_files(fetched, pin)
    except ModelIntegrityError as exc:
        return _heal(pin, cache_dir, fetched, exc), True
    return fetched, True


_READ_ONLY_ERRNOS = frozenset({errno.EROFS, errno.EACCES, errno.EPERM})


@contextmanager
def _cache_writes(
    repo_id: str, reason: str = "model cache is read-only"
) -> Iterator[None]:
    """Refuse with a coded error when a write fails for lack of permission."""
    try:
        yield
    except OSError as exc:
        if exc.errno not in _READ_ONLY_ERRNOS:
            raise
        raise ModelIntegrityError(repo_id, f"{reason} ({type(exc).__name__})") from exc


def _refuse_offline(pin: ModelPin, cache_dir: Path) -> None:
    """Raise the offline refusal for a snapshot that is missing or fails verification."""
    snapshot = _snapshot_dir(cache_dir, pin.repo_id, pin.revision)
    if not snapshot.is_dir():
        raise ModelIntegrityError(pin.repo_id, _OFFLINE_MISSING)
    verify_files(snapshot, pin)


def fetch_pinned_snapshot(
    pin: ModelPin, cache_dir: Path | None = None
) -> tuple[Path, bool]:
    """Like :func:`ensure_pinned_snapshot`, also reporting whether files were fetched.

    A cached snapshot that verifies is returned without any network call
    or lock, so a Hub outage never refuses a verified on-disk model.
    Offline, a missing or mismatched snapshot is refused before any lock or
    write, so a read-only model cache gets the coded refusal too.
    """
    cache = cache_dir or _default_cache_dir()
    local = _local_verified(pin, cache)
    if local is not None:
        return local, False
    if _is_offline():
        _refuse_offline(pin, cache)
    with _cache_writes(pin.repo_id), _repo_lock(cache, pin.repo_id):
        return _fetch_verified(pin, cache)


def ensure_pinned_snapshot(pin: ModelPin, cache_dir: Path | None = None) -> Path:
    """Return a local directory holding exactly the verified files of ``pin``.

    Uses the cached snapshot when it verifies; otherwise fetches the pinned
    files at ``pin.revision`` under a per-repository file lock, with one
    purge-and-refetch on a mismatch. Raises :class:`ModelIntegrityError`
    when the files cannot be obtained or still fail verification.
    """
    return fetch_pinned_snapshot(pin, cache_dir)[0]


def _load_user_pins(pins_path: Path, model_id: str) -> dict[str, dict[str, str]]:
    """Return the recorded user pins; a missing file is empty, an unreadable or malformed one is refused."""
    if not pins_path.exists():
        return {}
    try:
        pins = json.loads(pins_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ModelIntegrityError(model_id, _USER_PINS_CORRUPT) from exc
    if not isinstance(pins, dict):
        raise ModelIntegrityError(model_id, _USER_PINS_CORRUPT)
    return pins


def _save_user_pin(
    pins_path: Path, repo_id: str, revision: str, files: dict[str, str]
) -> None:
    """Add one record to the user-pins file atomically, under the file's own lock."""
    with _file_lock(pins_path.with_name(pins_path.name + ".lock")):
        pins = _load_user_pins(pins_path, repo_id)
        pins[f"{repo_id}@{revision}"] = files
        fd, tmp = tempfile.mkstemp(
            dir=pins_path.parent, prefix=f".{pins_path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(pins, fh, indent=2, sort_keys=True)
            os.chmod(tmp, 0o600)
            os.replace(tmp, pins_path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


def _user_model_pin(repo_id: str, revision: str, files: dict[str, str]) -> ModelPin:
    return ModelPin(repo_id, revision, "hf-transformers", MappingProxyType(files))


def _user_pin(pins_path: Path, repo_id: str, revision: str) -> ModelPin | None:
    files = _load_user_pins(pins_path, repo_id).get(f"{repo_id}@{revision}")
    if files is None:
        return None
    return _user_model_pin(repo_id, revision, files)


def _hub_digests(repo_id: str, revision: str) -> dict[str, tuple[str, str]]:
    """Return the Hub's digest per file at ``revision``: ``("sha256"|"git-sha1", hex)``."""
    from huggingface_hub import HfApi

    try:
        info = HfApi().model_info(repo_id, revision=revision, files_metadata=True)
    except Exception as exc:
        if not _is_hub_failure(exc):
            raise
        raise ModelIntegrityError(
            repo_id, f"Hub file metadata could not be fetched ({type(exc).__name__})"
        ) from exc
    if info.sha != revision:
        raise ModelIntegrityError(repo_id, "Hub metadata is for a different revision")
    digests: dict[str, tuple[str, str]] = {}
    for sibling in info.siblings or []:
        if sibling.lfs is not None:
            digests[sibling.rfilename] = ("sha256", sibling.lfs.sha256)
        elif sibling.blob_id:
            digests[sibling.rfilename] = ("git-sha1", sibling.blob_id)
    return digests


def _record_first_use(
    repo_id: str, revision: str, cache_dir: Path, pins_path: Path
) -> ModelPin:
    """Download a user-pinned model, check it against the Hub's metadata, record its hashes.

    Refused offline: a pre-populated cache is not a trustworthy first
    source. Online, only this revision's cached snapshot is purged first and
    the download is forced, so the record comes from a fresh download. Each
    file is read once; the SHA256 forms the record and seeds the
    verified-file cache. Every Hub file matching the download patterns must
    have arrived, so an incomplete download is never recorded.
    """
    from huggingface_hub.utils import filter_repo_objects

    if _is_offline():
        raise ModelIntegrityError(repo_id, _OFFLINE_FIRST_USE)
    manifest = MODEL_PINS.get(repo_id)
    if manifest is not None and manifest.revision == revision:
        raise ModelIntegrityError(
            repo_id, "revision is manifest-pinned, not user-pinned"
        )
    hub = _hub_digests(repo_id, revision)
    snapshot = _refetch(
        repo_id,
        revision,
        _snapshot_dir(cache_dir, repo_id, revision),
        cache_dir,
        list(_USER_PIN_PATTERNS),
    )
    files: dict[str, str] = {}
    for rel, path in sorted(_entries(snapshot)):
        if not any(fnmatch(rel, pat) for pat in _USER_PIN_PATTERNS):
            raise ModelIntegrityError(repo_id, f"unpinned file {rel!r} is present")
        sha256, identity, git_sha1 = _hash_file(path, git_blob=True)
        kind, expected = hub.get(rel, ("", ""))
        if identity is None or expected != (sha256 if kind == "sha256" else git_sha1):
            raise ModelIntegrityError(
                repo_id, f"file {rel!r} does not match the Hub's metadata"
            )
        _remember(path, identity, sha256)
        files[rel] = sha256
    missing = sorted(
        rel
        for rel in filter_repo_objects(hub, allow_patterns=list(_USER_PIN_PATTERNS))
        if rel not in files
    )
    if missing:
        raise ModelIntegrityError(
            repo_id, f"file {missing[0]!r} listed by the Hub is missing from the download"
        )
    if not any(rel.endswith(".safetensors") for rel in files):
        raise ModelIntegrityError(repo_id, "revision has no safetensors weights")
    pin = _user_model_pin(repo_id, revision, files)
    verify_files(snapshot, pin)
    with _cache_writes(repo_id, "user pins file is not writable"):
        _save_user_pin(pins_path, repo_id, revision, files)
    return pin


def fetch_user_pinned_snapshot(
    repo_id: str, revision: str, cache_dir: Path | None = None
) -> tuple[Path, bool]:
    """Like :func:`ensure_user_pinned_snapshot`, also reporting whether files were fetched."""
    cache = cache_dir or _default_cache_dir()
    pins_path = user_pins_path()
    pin = _user_pin(pins_path, repo_id, revision)
    first_use = False
    if pin is None:
        if _is_offline():
            raise ModelIntegrityError(repo_id, _OFFLINE_FIRST_USE)
        with _cache_writes(repo_id), _repo_lock(cache, repo_id):
            pin = _user_pin(pins_path, repo_id, revision)
            if pin is None:
                pin = _record_first_use(repo_id, revision, cache, pins_path)
                first_use = True
    snapshot, fetched = fetch_pinned_snapshot(pin, cache)
    return snapshot, fetched or first_use


def ensure_user_pinned_snapshot(
    repo_id: str, revision: str, cache_dir: Path | None = None
) -> Path:
    """Return a verified local directory for a commit-pinned model outside the manifest.

    On first use (online only) safetensors weights plus config and tokenizer
    files are fetched, checked against the Hub's metadata, and their SHA256
    recorded at :func:`user_pins_path`; every later load verifies against
    that record exactly like a manifest pin.
    """
    return fetch_user_pinned_snapshot(repo_id, revision, cache_dir)[0]


def ensure_model_snapshot(spec: str, cache_dir: Path | None = None) -> Path:
    """Resolve a model spec and return its verified local directory.

    Raises :class:`UnpinnedModelError` for an unpinned spec and
    :class:`ModelIntegrityError` when the files cannot be verified.
    """
    resolved = parse_model_spec(spec)
    if isinstance(resolved, ModelPin):
        return ensure_pinned_snapshot(resolved, cache_dir)
    return ensure_user_pinned_snapshot(*resolved, cache_dir=cache_dir)
