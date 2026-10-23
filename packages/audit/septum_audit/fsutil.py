"""Filesystem helpers for the ledger, anchor and status rewrites.

Tmp files are created exclusively and never followed, and their mode is set
explicitly, so a planted symlink or a foreign umask cannot redirect or widen a
write.
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import json
import os
import stat
from dataclasses import asdict
from pathlib import Path
from typing import IO, Any, Iterator

PRIVATE_MODE = 0o640
# O_NONBLOCK keeps a FIFO planted at a ledger path from blocking the open; the
# fstat check then refuses it. It has no effect on regular files.
_OPEN_GUARDS = getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)


def sibling(path: str | os.PathLike[str], suffix: str) -> Path:
    """``path`` with ``suffix`` appended to its full name (``audit.jsonl`` + ``.lock``)."""
    return Path(f"{path}{suffix}")


def create_tmp(path: Path) -> int:
    """Create ``path`` exclusively with mode 0o640 and return its file descriptor."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(path), flags, PRIVATE_MODE)
    try:
        os.fchmod(fd, PRIVATE_MODE)
    except BaseException:
        os.close(fd)
        raise
    return fd


def fsync_dir(path: Path) -> None:
    """Flush the directory entry of ``path`` so a completed rename survives a crash."""
    fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _require_regular(fd: int, path: Path, what: str) -> None:
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        raise OSError(errno.EINVAL, f"{what} is not a regular file: {path.name}")


def open_ledger_fd(path: Path, flags: int) -> int:
    """Open a ledger file without following a symlink and require a regular file.

    ``FileNotFoundError`` propagates unchanged so callers can treat a missing
    ledger as empty. Anything that is not a regular file raises ``OSError``.
    """
    fd = os.open(str(path), flags | _OPEN_GUARDS, PRIVATE_MODE)
    try:
        _require_regular(fd, path, "ledger path")
    except BaseException:
        os.close(fd)
        raise
    return fd


def open_ledger_file(path: Path, flags: int, mode: str) -> IO:
    """File object for :func:`open_ledger_fd`; the object owns the descriptor."""
    fd = open_ledger_fd(path, flags)
    try:
        return os.fdopen(fd, mode)
    except BaseException:
        os.close(fd)
        raise


def atomic_write_private(path: Path, body: str) -> None:
    """Replace ``path`` atomically with ``body``: exclusive tmp, fsync, rename, directory fsync."""
    tmp = sibling(path, ".tmp")
    tmp.unlink(missing_ok=True)
    try:
        fd = create_tmp(tmp)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        fsync_dir(path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def write_sidecar(path: Path, instance: Any) -> None:
    """Write a dataclass instance as sorted-key JSON through :func:`atomic_write_private`."""
    atomic_write_private(path, json.dumps(asdict(instance), sort_keys=True) + "\n")


def read_sidecar_json(path: Path, label: str) -> dict[str, Any] | None:
    """Return the parsed JSON object at ``path``, or ``None`` when the file does not exist.

    A body that is not valid JSON or not a JSON object raises ``ValueError`` naming the
    file only. Field validation stays with the caller.
    """
    try:
        with open_ledger_file(path, os.O_RDONLY, "r") as fh:
            raw = fh.read()
    except FileNotFoundError:
        return None
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"{label} is malformed: {path.name}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{label} is malformed: {path.name}")
    return data


@contextlib.contextmanager
def locked(path: Path) -> Iterator[None]:
    """Hold an exclusive advisory lock on ``path`` for the block.

    The lock file is opened without following a symlink and must be a regular
    file. The same helper serialises the sink's appends and the retention pass,
    across threads and processes.
    """
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT | _OPEN_GUARDS, PRIVATE_MODE)
    try:
        _require_regular(fd, path, "lock path")
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def nonblank(fh: IO[bytes]) -> Iterator[tuple[int, bytes, bytes]]:
    """Yield ``(line_number, raw_line, stripped_line)`` for every line that is not blank."""
    for number, raw in enumerate(fh, start=1):
        line = raw.strip()
        if line:
            yield number, raw, line
