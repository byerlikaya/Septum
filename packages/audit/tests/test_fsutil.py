"""Filesystem guards and modes: ledger, lock and tmp paths are never symlinks, FIFOs or devices, and modes are set explicitly."""

from __future__ import annotations

import asyncio
import os
import stat
from pathlib import Path
from typing import Callable

import pytest

from septum_audit import AuditRecord, JsonlFileSink, RetentionPolicy
from septum_audit import retention as retention_module
from septum_audit.anchor import read_anchor
from septum_audit.fsutil import atomic_write_private, create_tmp, locked, open_ledger_fd, open_ledger_file

from _helpers import NOW, bounded, seed


def _via_open_ledger_fd(path: Path) -> None:
    os.close(open_ledger_fd(path, os.O_RDONLY))


def _via_open_ledger_file(path: Path) -> None:
    open_ledger_file(path, os.O_RDONLY, "rb").close()


def _via_locked(path: Path) -> None:
    with locked(path):
        pass


def _via_create_tmp(path: Path) -> None:
    os.close(create_tmp(path))


OPENERS: dict[str, Callable[[Path], None]] = {
    "open_ledger_fd": _via_open_ledger_fd,
    "open_ledger_file": _via_open_ledger_file,
    "locked": _via_locked,
    "create_tmp": _via_create_tmp,
}


@pytest.mark.parametrize("opener", OPENERS.values(), ids=OPENERS.keys())
def test_a_symlink_is_refused_and_its_target_is_untouched(tmp_path: Path, opener):
    real = tmp_path / "real.jsonl"
    real.write_text("keep", encoding="utf-8")
    link = tmp_path / "audit.jsonl"
    link.symlink_to(real)

    with pytest.raises(OSError):
        opener(link)

    assert real.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("opener", OPENERS.values(), ids=OPENERS.keys())
def test_a_fifo_is_refused_without_blocking(tmp_path: Path, opener):
    fifo = tmp_path / "audit.jsonl"
    os.mkfifo(fifo)

    with bounded():
        with pytest.raises(OSError):
            opener(fifo)


@pytest.mark.parametrize("opener", OPENERS.values(), ids=OPENERS.keys())
def test_a_device_is_refused_as_a_non_regular_file(opener):
    with pytest.raises(OSError):
        opener(Path(os.devnull))


def test_create_tmp_does_not_create_through_a_dangling_symlink(tmp_path: Path):
    link = tmp_path / "dangling"
    link.symlink_to(tmp_path / "missing-target")

    with pytest.raises(FileExistsError):
        create_tmp(link)
    assert not (tmp_path / "missing-target").exists()


@pytest.mark.parametrize(
    ("kind", "operation"),
    [
        ("symlink", "write"),
        ("symlink", "read"),
        ("symlink", "retention"),
        ("fifo", "write"),
        ("fifo", "retention"),
        ("device", "read"),
    ],
)
def test_the_ledger_path_is_refused_by_sink_writes_reads_and_retention(tmp_path: Path, kind: str, operation: str):
    target = tmp_path / "audit.jsonl"
    real = tmp_path / "real.jsonl"
    if kind == "symlink":
        real.write_text("", encoding="utf-8")
        target.symlink_to(real)
    elif kind == "fifo":
        os.mkfifo(target)
    path = Path(os.devnull) if kind == "device" else target
    sink = JsonlFileSink(path)

    with bounded():
        with pytest.raises(OSError):
            if operation == "write":
                asyncio.run(sink.write(AuditRecord(id="x", source="s", event_type="e")))
            elif operation == "read":
                list(sink.read_all())
            else:
                retention_module.apply_retention_to_jsonl(path, RetentionPolicy(max_age_days=1), now=NOW)

    if kind == "symlink":
        assert real.read_text(encoding="utf-8") == ""


@pytest.mark.parametrize("umask", [0o022, 0o077])
def test_create_tmp_mode_is_exactly_0o640_whatever_the_umask(tmp_path: Path, umask: int):
    path = tmp_path / "fresh"
    previous = os.umask(umask)
    try:
        fd = create_tmp(path)
    finally:
        os.umask(previous)
    os.close(fd)

    assert oct(path.stat().st_mode & 0o777) == "0o640"


def test_locked_creates_the_parent_directory_private_and_the_lock_file_0o640(tmp_path: Path):
    lock = tmp_path / "new" / "audit.jsonl.lock"
    with locked(lock):
        pass

    assert stat.S_IMODE(lock.parent.stat().st_mode) == 0o750
    assert stat.S_IMODE(lock.stat().st_mode) == 0o640


def test_sink_creates_its_directory_private(tmp_path: Path):
    JsonlFileSink(tmp_path / "ledger-dir" / "audit.jsonl")
    assert stat.S_IMODE((tmp_path / "ledger-dir").stat().st_mode) == 0o750


async def test_anchor_and_ledger_keep_mode_0o640_after_a_prune(tmp_path: Path):
    from septum_audit.anchor import anchor_path

    target = tmp_path / "audit.jsonl"
    await seed(target, [10, 0.5])
    await JsonlFileSink(target).apply_retention(RetentionPolicy(max_age_days=2), now=NOW)

    assert read_anchor(target) is not None
    assert oct(anchor_path(target).stat().st_mode & 0o777) == "0o640"
    assert oct(target.stat().st_mode & 0o777) == "0o640"


def test_atomic_write_syncs_the_file_and_its_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from septum_audit import fsutil

    synced: list[int] = []
    real_fsync = os.fsync
    monkeypatch.setattr(fsutil.os, "fsync", lambda fd: (synced.append(fd), real_fsync(fd))[1])

    atomic_write_private(tmp_path / "state", "body\n")

    assert len(synced) == 2
    assert (tmp_path / "state").read_text() == "body\n"
