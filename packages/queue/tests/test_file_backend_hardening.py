"""Failure-mode tests for the file backend: undecodable entries, refused claims, directory faults."""

from __future__ import annotations

import errno
import itertools
import json
import logging
import os
import shutil
import threading
import time
from pathlib import Path

import pytest
from septum_queue import QueueError
from septum_queue import file_backend as file_backend_module
from septum_queue.base import MAX_ENTRY_BYTES
from septum_queue.file_backend import FileQueueBackend


def _dead_letter_originals(directory: Path) -> list[str]:
    """Original entry names in ``dead-letter/``; each file is named ``{ns}-{hex}-{original}``."""
    return [p.name.split("-", 2)[2] for p in directory.iterdir()]


def _fd_directory() -> str:
    """The per-process descriptor listing: ``/proc/self/fd`` on Linux, ``/dev/fd`` on macOS."""
    for candidate in ("/proc/self/fd", "/dev/fd"):
        if os.path.isdir(candidate):
            return candidate
    pytest.skip("no per-process descriptor directory on this platform")


def test_topic_with_path_traversal_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(QueueError, match="invalid topic"):
        FileQueueBackend(tmp_path, topic="../escape")


def test_topic_with_absolute_path_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(QueueError, match="invalid topic"):
        FileQueueBackend(tmp_path, topic="/etc/septum")


def test_constructor_recovers_orphans_from_processing(tmp_path: Path) -> None:
    """A crash that left a payload in processing/ should be reclaimed."""
    topic_root = tmp_path / "septum-test"
    processing = topic_root / "processing"
    processing.mkdir(parents=True)
    incoming = topic_root / "incoming"
    incoming.mkdir(parents=True)

    orphan = processing / "00000000000000000001-orphan.json"
    orphan.write_text(json.dumps({"hello": "world"}), encoding="utf-8")

    FileQueueBackend(tmp_path, topic="septum-test")

    assert not orphan.exists()
    recovered = list(incoming.iterdir())
    assert len(recovered) == 1
    assert recovered[0].name == "00000000000000000001-orphan.json"


@pytest.mark.asyncio
async def test_corrupted_payload_routes_to_dead_letter(tmp_path: Path) -> None:
    backend = FileQueueBackend(tmp_path, topic="dl-topic")

    bad_name = "00000000000000000001-bad.json"
    (tmp_path / "dl-topic" / "incoming" / bad_name).write_text(
        "not json {[", encoding="utf-8"
    )

    received = [m async for m in backend.consume(batch_size=1, block_ms=0)]

    assert received == []
    assert _dead_letter_originals(tmp_path / "dl-topic" / "dead-letter") == [bad_name]


UNDECODABLE_ENTRIES = {
    "deep_nesting": '{"id":"p","attributes":' + "[" * 20000 + "]" * 20000 + "}",
    "long_int": '{"id":"p","attributes":{"n":' + "1" * 5000 + "}}",
    "oversized": '{"id":"p","blob":"' + "a" * (MAX_ENTRY_BYTES + 1) + '"}',
}


@pytest.mark.asyncio
@pytest.mark.parametrize("label", sorted(UNDECODABLE_ENTRIES))
async def test_undecodable_entry_is_quarantined_and_next_message_delivered(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, label: str
) -> None:
    backend = FileQueueBackend(tmp_path, topic="poison")
    bad_name = "00000000000000000001-bad.json"
    (tmp_path / "poison" / "incoming" / bad_name).write_text(
        UNDECODABLE_ENTRIES[label], encoding="utf-8"
    )
    await backend.publish({"id": "good"})

    received = [m async for m in backend.consume(batch_size=5, block_ms=0)]

    assert [m.payload for m in received] == [{"id": "good"}]
    assert _dead_letter_originals(tmp_path / "poison" / "dead-letter") == [bad_name]
    assert all(
        p.name != bad_name for p in (tmp_path / "poison" / "processing").iterdir()
    )
    assert "[[[" not in caplog.text
    assert "aaaa" not in caplog.text
    assert "1111" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("label", sorted(UNDECODABLE_ENTRIES))
async def test_undecodable_entry_does_not_loop_across_restart(
    tmp_path: Path, label: str
) -> None:
    """A crash mid-claim leaves the entry in processing/; restarts must not re-wedge on it."""
    topic_root = tmp_path / "restart"
    (topic_root / "processing").mkdir(parents=True)
    bad_name = "00000000000000000001-bad.json"
    (topic_root / "processing" / bad_name).write_text(
        UNDECODABLE_ENTRIES[label], encoding="utf-8"
    )

    first = FileQueueBackend(tmp_path, topic="restart")
    await first.publish({"id": "good"})
    received = [m async for m in first.consume(batch_size=5, block_ms=0)]
    assert [m.payload for m in received] == [{"id": "good"}]

    second = FileQueueBackend(tmp_path, topic="restart")
    received = [m async for m in second.consume(batch_size=5, block_ms=0)]

    assert [m.payload for m in received] == [{"id": "good"}]
    assert _dead_letter_originals(topic_root / "dead-letter") == [bad_name]
    assert all(p.name != bad_name for p in (topic_root / "incoming").iterdir())
    assert all(p.name != bad_name for p in (topic_root / "processing").iterdir())


EXPECTED_LOGGED_TYPE = {
    "deep_nesting": "RecursionError",
    "long_int": "ValueError",
    "oversized": "_OversizedEntryError",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("label", sorted(UNDECODABLE_ENTRIES))
async def test_quarantine_log_names_exception_type_only(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, label: str
) -> None:
    backend = FileQueueBackend(tmp_path, topic="logtype")
    bad_name = "00000000000000000001-bad.json"
    (tmp_path / "logtype" / "incoming" / bad_name).write_text(
        UNDECODABLE_ENTRIES[label], encoding="utf-8"
    )

    [m async for m in backend.consume(batch_size=1, block_ms=0)]

    assert (
        f"Quarantined undecodable queue message {bad_name!r}: {EXPECTED_LOGGED_TYPE[label]}"
        in caplog.text
    )


@pytest.mark.asyncio
async def test_non_utf8_entry_is_quarantined_without_leaking_bytes(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    backend = FileQueueBackend(tmp_path, topic="bin")
    bad_name = "00000000000000000001-bin.json"
    (tmp_path / "bin" / "incoming" / bad_name).write_bytes(b"\xff\xfesecret-sentinel")
    await backend.publish({"id": "good"})

    received = [m async for m in backend.consume(batch_size=5, block_ms=0)]

    assert [m.payload for m in received] == [{"id": "good"}]
    assert _dead_letter_originals(tmp_path / "bin" / "dead-letter") == [bad_name]
    assert "UnicodeDecodeError" in caplog.text
    assert "secret-sentinel" not in caplog.text


@pytest.mark.asyncio
async def test_entry_of_exactly_the_size_cap_is_still_decoded(tmp_path: Path) -> None:
    backend = FileQueueBackend(tmp_path, topic="edge")
    head, tail = '{"blob":"', '"}'
    filler = "a" * (MAX_ENTRY_BYTES - len(head) - len(tail))
    name = "00000000000000000001-edge.json"
    path = tmp_path / "edge" / "incoming" / name
    path.write_text(head + filler + tail, encoding="utf-8")
    assert path.stat().st_size == MAX_ENTRY_BYTES

    received = [m async for m in backend.consume(batch_size=1, block_ms=0)]

    assert [m.id for m in received] == [name]
    assert not list((tmp_path / "edge" / "dead-letter").iterdir())


@pytest.mark.asyncio
async def test_unreadable_entry_is_quarantined_and_next_delivered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    backend = FileQueueBackend(tmp_path, topic="oserr")
    bad_name = "00000000000000000001-bad.json"
    (tmp_path / "oserr" / "incoming" / bad_name).write_text("{}", encoding="utf-8")
    await backend.publish({"id": "good"})
    real_open = os.open

    def failing_open(path, flags, *args, **kwargs):
        if Path(path).name == bad_name:
            raise PermissionError(13, "denied /secret-sentinel")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", failing_open)

    received = [m async for m in backend.consume(batch_size=5, block_ms=0)]

    assert [m.payload for m in received] == [{"id": "good"}]
    assert _dead_letter_originals(tmp_path / "oserr" / "dead-letter") == [bad_name]
    assert "PermissionError" in caplog.text
    assert "secret-sentinel" not in caplog.text


@pytest.mark.asyncio
async def test_quarantine_log_truncates_long_entry_name(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    backend = FileQueueBackend(tmp_path, topic="longname")
    bad_name = "00000000000000000001-" + "x" * 150 + ".json"
    (tmp_path / "longname" / "incoming" / bad_name).write_text("not json", encoding="utf-8")

    [m async for m in backend.consume(batch_size=1, block_ms=0)]

    assert repr(bad_name)[:64] in caplog.text
    assert "x" * 100 not in caplog.text


@pytest.mark.asyncio
async def test_failed_quarantine_move_is_logged_truncated_and_does_not_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    backend = FileQueueBackend(tmp_path, topic="movefail")
    bad_name = "00000000000000000001-" + "y" * 150 + ".json"
    (tmp_path / "movefail" / "incoming" / bad_name).write_text("not json", encoding="utf-8")
    await backend.publish({"id": "good"})
    real_replace = os.replace

    def failing_replace(src, dst):
        if Path(dst).parent.name == "dead-letter":
            raise PermissionError(13, "denied")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", failing_replace)

    with caplog.at_level(logging.ERROR):
        received = [m async for m in backend.consume(batch_size=5, block_ms=0)]

    assert [m.payload for m in received] == [{"id": "good"}]
    assert "Could not quarantine queue message" in caplog.text
    assert "PermissionError" in caplog.text
    assert repr(bad_name)[:64] in caplog.text
    assert "y" * 100 not in caplog.text


class _FifoUnblocker:
    """Releases a thread stuck opening a FIFO for reading, and records that it had to."""

    def __init__(self, *fifos: Path, delay: float = 1.5) -> None:
        self.released = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(fifos, delay), daemon=True)

    def _run(self, fifos: tuple[Path, ...], delay: float) -> None:
        if self._stop.wait(delay):
            return
        while not self._stop.is_set():
            fd = None
            for fifo in fifos:
                try:
                    fd = os.open(fifo, os.O_RDWR)
                    break
                except OSError:
                    continue
            if fd is None:
                self._stop.wait(0.05)
                continue
            self.released = True
            self._stop.wait()
            os.close(fd)
            return

    def __enter__(self) -> "_FifoUnblocker":
        self._thread.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self._stop.set()
        self._thread.join(timeout=5)


@pytest.mark.asyncio
async def test_fifo_entry_is_quarantined_without_blocking(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    backend = FileQueueBackend(tmp_path, topic="fifo")
    bad_name = "00000000000000000001-fifo.json"
    fifo = tmp_path / "fifo" / "incoming" / bad_name
    claimed = tmp_path / "fifo" / "processing" / bad_name
    os.mkfifo(fifo)
    await backend.publish({"id": "good"})

    with _FifoUnblocker(fifo, claimed) as unblocker:
        started = time.monotonic()
        received = [m async for m in backend.consume(batch_size=5, block_ms=0)]
        elapsed = time.monotonic() - started

    assert not unblocker.released, "opening the FIFO blocked until a writer appeared"
    assert elapsed < 1.0
    assert [m.payload for m in received] == [{"id": "good"}]
    assert _dead_letter_originals(tmp_path / "fifo" / "dead-letter") == [bad_name]
    assert "_NotRegularFileError" in caplog.text


@pytest.mark.asyncio
async def test_empty_queue_with_zero_block_returns_immediately(tmp_path: Path) -> None:
    import asyncio

    backend = FileQueueBackend(tmp_path, topic="drain")

    async def drain() -> list:
        return [m async for m in backend.consume(batch_size=3, block_ms=0)]

    assert await asyncio.wait_for(drain(), timeout=2) == []


@pytest.mark.asyncio
async def test_deadline_is_checked_after_each_quarantine(tmp_path: Path) -> None:
    ticks = itertools.count()
    backend = FileQueueBackend(tmp_path, topic="deadline", clock=lambda: float(next(ticks)))
    incoming = tmp_path / "deadline" / "incoming"
    for index in range(10):
        (incoming / f"{index:020d}-bad.json").write_text("not json", encoding="utf-8")

    received = [m async for m in backend.consume(batch_size=1, block_ms=100)]

    assert received == []
    assert len(list((tmp_path / "deadline" / "dead-letter").iterdir())) == 1
    assert len(list(incoming.iterdir())) == 9


@pytest.mark.asyncio
async def test_incoming_is_listed_once_per_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = FileQueueBackend(tmp_path, topic="listing")
    incoming = tmp_path / "listing" / "incoming"
    for index in range(5):
        (incoming / f"{index:020d}-bad.json").write_text("not json", encoding="utf-8")
    await backend.publish({"id": "good"})
    listings: list[Path] = []
    real_iterdir = Path.iterdir

    def counting_iterdir(self: Path):
        if self == incoming:
            listings.append(self)
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", counting_iterdir)

    received = [m async for m in backend.consume(batch_size=5, block_ms=0)]

    assert [m.payload for m in received] == [{"id": "good"}]
    assert len(listings) == 2  # the pass that handles all six entries, then the empty pass


def test_reclaim_log_truncates_long_entry_name(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    processing = tmp_path / "reclaim" / "processing"
    processing.mkdir(parents=True)
    long_name = "00000000000000000001-" + "z" * 150 + ".json"
    (processing / long_name).write_text("{}", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        FileQueueBackend(tmp_path, topic="reclaim")

    assert repr(long_name)[:64] in caplog.text
    assert "z" * 100 not in caplog.text


@pytest.mark.asyncio
async def test_symlink_to_device_is_quarantined_and_never_followed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    backend = FileQueueBackend(tmp_path, topic="devlink")
    bad_name = "00000000000000000001-dev.json"
    os.symlink("/dev/zero", tmp_path / "devlink" / "incoming" / bad_name)
    await backend.publish({"id": "good"})

    received = [m async for m in backend.consume(batch_size=5, block_ms=0)]

    assert [m.payload for m in received] == [{"id": "good"}]
    (moved,) = list((tmp_path / "devlink" / "dead-letter").iterdir())
    assert moved.name.endswith(f"-{bad_name}")
    assert moved.is_symlink()
    assert os.readlink(moved) == "/dev/zero"
    assert "OSError" in caplog.text


@pytest.mark.asyncio
async def test_read_is_capped_at_limit_plus_one_byte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reads never exceed the cap plus one byte, however large the file is on disk."""
    monkeypatch.setattr(file_backend_module, "MAX_ENTRY_BYTES", 1024)
    backend = FileQueueBackend(tmp_path, topic="grow")
    bad_name = "00000000000000000001-grow.json"
    (tmp_path / "grow" / "incoming" / bad_name).write_text(
        '{"blob":"' + "a" * 10_000 + '"}', encoding="utf-8"
    )
    await backend.publish({"id": "good"})
    requested: list[int] = []
    returned_total = 0
    real_read = os.read

    def spy_read(fd, size):
        nonlocal returned_total
        requested.append(size)
        chunk = real_read(fd, size)
        returned_total += len(chunk)
        return chunk

    monkeypatch.setattr(os, "read", spy_read)

    received = [m async for m in backend.consume(batch_size=5, block_ms=0)]

    assert [m.payload for m in received] == [{"id": "good"}]
    assert requested[0] == 1025
    assert returned_total < 10_000
    assert _dead_letter_originals(tmp_path / "grow" / "dead-letter") == [bad_name]


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["[1,2]", '"x"', "null", "42"])
async def test_non_object_json_is_quarantined(tmp_path: Path, body: str) -> None:
    backend = FileQueueBackend(tmp_path, topic="shape")
    bad_name = "00000000000000000001-shape.json"
    (tmp_path / "shape" / "incoming" / bad_name).write_text(body, encoding="utf-8")
    await backend.publish({"id": "good"})

    received = [m async for m in backend.consume(batch_size=5, block_ms=0)]

    assert [m.payload for m in received] == [{"id": "good"}]
    assert _dead_letter_originals(tmp_path / "shape" / "dead-letter") == [bad_name]


@pytest.mark.asyncio
async def test_backlog_of_bad_entries_returns_within_block_ms(tmp_path: Path) -> None:
    backend = FileQueueBackend(tmp_path, topic="flood")
    incoming = tmp_path / "flood" / "incoming"
    for index in range(3000):
        (incoming / f"{index:020d}-bad.json").write_text("not json", encoding="utf-8")

    started = time.monotonic()
    received = [m async for m in backend.consume(batch_size=1, block_ms=100)]
    elapsed = time.monotonic() - started

    assert received == []
    assert elapsed < 2.0
    assert list((tmp_path / "flood" / "dead-letter").iterdir())


@pytest.mark.asyncio
async def test_directories_named_like_entries_do_not_leak_descriptors(tmp_path: Path) -> None:
    backend = FileQueueBackend(tmp_path, topic="dirs")
    incoming = tmp_path / "dirs" / "incoming"
    for index in range(50):
        (incoming / f"{index:020d}-d.json").mkdir()
    fd_dir = _fd_directory()
    before = len(os.listdir(fd_dir))

    received = [m async for m in backend.consume(batch_size=5, block_ms=0)]

    assert received == []
    assert len(os.listdir(fd_dir)) == before
    assert len(list((tmp_path / "dirs" / "dead-letter").iterdir())) == 50


@pytest.mark.asyncio
async def test_name_collisions_do_not_wedge_the_consumer_across_restarts(
    tmp_path: Path,
) -> None:
    backend = FileQueueBackend(tmp_path, topic="collide")
    incoming = tmp_path / "collide" / "incoming"
    for _ in range(2):
        colliding_dir = incoming / "0.json"
        colliding_dir.mkdir()
        (colliding_dir / "inner").write_text("x", encoding="utf-8")
        assert [m async for m in backend.consume(batch_size=5, block_ms=0)] == []
    (incoming / "0.json").write_text('{"id":"from-file"}', encoding="utf-8")
    await backend.publish({"id": "good"})

    received = [m async for m in backend.consume(batch_size=5, block_ms=0)]

    assert [m.payload for m in received] == [{"id": "from-file"}, {"id": "good"}]
    dead_originals = _dead_letter_originals(tmp_path / "collide" / "dead-letter")
    assert sorted(dead_originals) == ["0.json", "0.json"]

    restarted = FileQueueBackend(tmp_path, topic="collide")
    replayed = [m async for m in restarted.consume(batch_size=5, block_ms=0)]
    assert sorted(m.payload["id"] for m in replayed) == ["from-file", "good"]


def test_reclaim_keeps_both_entries_when_incoming_name_is_taken(tmp_path: Path) -> None:
    topic_root = tmp_path / "rc"
    (topic_root / "processing").mkdir(parents=True)
    (topic_root / "incoming").mkdir()
    (topic_root / "processing" / "0.json").write_text('{"id":"orphan"}', encoding="utf-8")
    (topic_root / "incoming" / "0.json").write_text('{"id":"waiting"}', encoding="utf-8")

    FileQueueBackend(tmp_path, topic="rc")

    names = sorted(p.name for p in (topic_root / "incoming").iterdir())
    assert len(names) == 2
    assert "0.json" in names
    contents = {(topic_root / "incoming" / n).read_text(encoding="utf-8") for n in names}
    assert contents == {'{"id":"orphan"}', '{"id":"waiting"}'}


def test_repeated_reclaim_collisions_never_overwrite_waiting_entries(tmp_path: Path) -> None:
    topic_root = tmp_path / "rc2"
    (topic_root / "processing").mkdir(parents=True)
    (topic_root / "incoming").mkdir()
    (topic_root / "incoming" / "0.json").write_text('{"id":"waiting"}', encoding="utf-8")

    for index in range(3):
        (topic_root / "processing" / "0.json").write_text(
            json.dumps({"id": f"orphan-{index}"}), encoding="utf-8"
        )
        FileQueueBackend(tmp_path, topic="rc2")

    incoming = topic_root / "incoming"
    assert all(p.suffix == ".json" for p in incoming.iterdir())
    ids = sorted(json.loads(p.read_text(encoding="utf-8"))["id"] for p in incoming.iterdir())
    assert ids == ["orphan-0", "orphan-1", "orphan-2", "waiting"]
    assert not list((topic_root / "processing").iterdir())


def test_reclaim_does_not_replace_a_dangling_symlink_with_the_same_name(tmp_path: Path) -> None:
    topic_root = tmp_path / "rc3"
    (topic_root / "processing").mkdir(parents=True)
    (topic_root / "incoming").mkdir()
    os.symlink(tmp_path / "nowhere", topic_root / "incoming" / "0.json")
    (topic_root / "processing" / "0.json").write_text('{"id":"orphan"}', encoding="utf-8")

    FileQueueBackend(tmp_path, topic="rc3")

    assert (topic_root / "incoming" / "0.json").is_symlink()
    others = [p for p in (topic_root / "incoming").iterdir() if not p.is_symlink()]
    assert [json.loads(p.read_text(encoding="utf-8")) for p in others] == [{"id": "orphan"}]


@pytest.mark.asyncio
async def test_nack_requeue_mints_fresh_filename(tmp_path: Path) -> None:
    backend = FileQueueBackend(tmp_path, topic="rq")
    original = await backend.publish({"x": 1})

    async for message in backend.consume(batch_size=1, block_ms=0):
        await backend.nack(message.id, requeue=True)
        break

    incoming_names = sorted(p.name for p in (tmp_path / "rq" / "incoming").iterdir())
    assert original not in incoming_names
    assert len(incoming_names) == 1


async def _consume_all(backend: FileQueueBackend) -> list:
    return [m async for m in backend.consume(batch_size=5, block_ms=0)]


def _incoming_count(tmp_path: Path, topic: str) -> int:
    return len(list((tmp_path / topic / "incoming").iterdir()))


def _refuse_replace(
    monkeypatch: pytest.MonkeyPatch,
    *,
    error: int = errno.EACCES,
    name: str | None = None,
    src_dir: str | None = None,
    dst_dir: str | None = None,
    either_dir: str | None = None,
) -> list[tuple[Path, Path]]:
    """Make matching ``os.replace`` calls raise ``OSError(error)``; returns the matched renames.

    A rename matches when every given filter holds: the source file ``name``, the
    parent directory name of the source or destination, or of either of them.
    """
    real_replace = os.replace
    matched: list[tuple[Path, Path]] = []

    def replace(src, dst):
        source, target = Path(src), Path(dst)
        if (
            (name is None or source.name == name)
            and (src_dir is None or source.parent.name == src_dir)
            and (dst_dir is None or target.parent.name == dst_dir)
            and (either_dir is None or either_dir in (source.parent.name, target.parent.name))
        ):
            matched.append((source, target))
            raise OSError(error, os.strerror(error), str(src))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", replace)
    return matched


def _claims(matched: list[tuple[Path, Path]]) -> int:
    """How many matched renames were claim attempts (into processing/)."""
    return sum(1 for _, target in matched if target.parent.name == "processing")


def _restore_directory(path: Path) -> None:
    if path.is_file():
        path.unlink()
    path.mkdir(exist_ok=True)


def _rename_fails_with(number: int):
    def apply(root: Path, directory: str, monkeypatch: pytest.MonkeyPatch):
        if directory == "dead-letter":
            _refuse_replace(monkeypatch, dst_dir="processing")
            _refuse_replace(monkeypatch, error=number, dst_dir="dead-letter")
        elif directory == "processing":
            _refuse_replace(monkeypatch, error=number, dst_dir="processing")
        else:
            _refuse_replace(monkeypatch, error=number, src_dir="incoming")
        return monkeypatch.undo

    return apply


def _directory_removed(root: Path, directory: str, monkeypatch: pytest.MonkeyPatch):
    shutil.rmtree(root / directory)
    if directory == "dead-letter":
        _refuse_replace(monkeypatch, dst_dir="processing")
    return lambda: (_restore_directory(root / directory), monkeypatch.undo())


def _directory_replaced_by_file(root: Path, directory: str, monkeypatch: pytest.MonkeyPatch):
    shutil.rmtree(root / directory)
    (root / directory).write_text("not a directory", encoding="utf-8")
    if directory == "dead-letter":
        _refuse_replace(monkeypatch, dst_dir="processing")
    return lambda: (_restore_directory(root / directory), monkeypatch.undo())


def _directory_made_read_only(root: Path, directory: str, monkeypatch: pytest.MonkeyPatch):
    if os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    target = root / directory
    target.chmod(0o500)
    return lambda: target.chmod(0o755)


def _directory_denies_access(bit: int):
    def apply(root: Path, directory: str, monkeypatch: pytest.MonkeyPatch):
        target = root / directory
        real_access = os.access

        def access(path, mode, *args, **kwargs):
            if Path(path) == target and mode & bit:
                return False
            return real_access(path, mode, *args, **kwargs)

        _refuse_replace(monkeypatch, either_dir=directory)
        monkeypatch.setattr(os, "access", access)
        return monkeypatch.undo

    return apply


_DISK_ERRNOS = [errno.ENOSPC, errno.EDQUOT, errno.EROFS, errno.EIO, errno.EXDEV]

DIRECTORY_FAULTS = [
    pytest.param(directory, _rename_fails_with(number), id=f"{directory}-{errno.errorcode[number]}")
    for directory in ("processing", "incoming", "dead-letter")
    for number in _DISK_ERRNOS
] + [
    pytest.param(directory, mechanism, id=f"{directory}-{label}")
    for directory in ("processing", "dead-letter")
    for label, mechanism in (
        ("removed", _directory_removed),
        ("replaced_by_file", _directory_replaced_by_file),
    )
] + [
    pytest.param(directory, mechanism, id=f"{directory}-{label}")
    for directory in ("processing", "incoming")
    for label, mechanism in (
        ("read_only", _directory_made_read_only),
        ("no_write_access", _directory_denies_access(os.W_OK)),
        ("no_search_access", _directory_denies_access(os.X_OK)),
    )
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("directory", "break_directory"), DIRECTORY_FAULTS)
async def test_unusable_directory_raises_and_keeps_every_entry_deliverable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, directory: str, break_directory
) -> None:
    root = tmp_path / "faulty"
    backend = FileQueueBackend(tmp_path, topic="faulty", clock=lambda: 1000.0)
    for index in range(2):
        await backend.publish({"id": f"m{index}"})
    repair = break_directory(root, directory, monkeypatch)
    try:
        with pytest.raises(QueueError):
            await _consume_all(backend)

        dead_letter = root / "dead-letter"
        assert _incoming_count(tmp_path, "faulty") == 2
        assert not (dead_letter.is_dir() and any(dead_letter.iterdir()))
    finally:
        repair()
        monkeypatch.undo()

    received = await _consume_all(backend)

    assert sorted(m.payload["id"] for m in received) == ["m0", "m1"]


@pytest.mark.asyncio
async def test_lost_race_is_not_a_fault_even_when_processing_is_unusable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = FileQueueBackend(tmp_path, topic="racebroken")
    await backend.publish({"id": "x"})
    (source,) = list((tmp_path / "racebroken" / "incoming").iterdir())
    shutil.rmtree(tmp_path / "racebroken" / "processing")
    real_replace = os.replace

    def rival_wins(src, dst):
        if Path(src) == source:
            os.unlink(src)
            raise FileNotFoundError(2, "taken", str(src))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", rival_wins)

    assert await _consume_all(backend) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("block_ms", [0, 200])
async def test_claim_failing_with_source_present_neither_spins_nor_changes_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, block_ms: int
) -> None:
    backend = FileQueueBackend(tmp_path, topic="transient")
    await backend.publish({"id": "x"})
    attempts = _refuse_replace(monkeypatch, error=errno.ENOENT, src_dir="incoming")

    started = time.monotonic()
    received = [m async for m in backend.consume(batch_size=1, block_ms=block_ms)]
    elapsed = time.monotonic() - started

    assert received == []
    assert elapsed < 2.0
    assert 1 <= len(attempts) <= 20
    assert _incoming_count(tmp_path, "transient") == 1
    assert list((tmp_path / "transient" / "dead-letter").iterdir()) == []

    monkeypatch.undo()

    assert [m.payload for m in await _consume_all(backend)] == [{"id": "x"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("error_number", [errno.EACCES, errno.EPERM, errno.EBUSY, errno.EINVAL])
async def test_entry_level_errno_is_quarantined_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error_number: int
) -> None:
    backend = FileQueueBackend(tmp_path, topic="entry")
    stuck = "00000000000000000001-stuck.json"
    (tmp_path / "entry" / "incoming" / stuck).write_text('{"id":"stuck"}', encoding="utf-8")
    await backend.publish({"id": "good"})
    _refuse_replace(monkeypatch, error=error_number, name=stuck, dst_dir="processing")

    received = await _consume_all(backend)

    assert [m.payload for m in received] == [{"id": "good"}]
    assert _dead_letter_originals(tmp_path / "entry" / "dead-letter") == [stuck]


@pytest.mark.asyncio
async def test_repeated_permission_refusals_in_one_pass_raise_before_quarantining(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = FileQueueBackend(tmp_path, topic="repeat", clock=lambda: 1000.0)
    for index in range(5):
        await backend.publish({"id": f"m{index}"})
    _refuse_replace(monkeypatch, src_dir="incoming")

    with pytest.raises(QueueError):
        await _consume_all(backend)

    monkeypatch.undo()

    # The three refused entries (two quarantined, one that triggered the raise) are
    # cooling down; the two untouched ones flow on.
    assert len(await _consume_all(backend)) == 2
    assert len(backend._refused_until) == 3


@pytest.mark.asyncio
async def test_regular_file_permission_refusals_raise_as_a_directory_fault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = FileQueueBackend(tmp_path, topic="regfiles")
    for index in range(3):
        await backend.publish({"id": f"m{index}"})
    _refuse_replace(monkeypatch, src_dir="incoming")

    with pytest.raises(QueueError):
        await _consume_all(backend)

    assert len(backend._refused_until) == 3


@pytest.mark.asyncio
async def test_files_owned_by_another_uid_are_entry_faults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In a sticky incoming/, a refused file owned by another uid is not our directory's fault."""
    backend = FileQueueBackend(tmp_path, topic="otheruid")
    for index in range(3):
        await backend.publish({"id": f"m{index}"})
    _refuse_replace(monkeypatch, src_dir="incoming")
    monkeypatch.setattr(os, "geteuid", lambda: os.getuid() + 1)

    assert await _consume_all(backend) == []
    assert len(backend._refused_until) == 3


@pytest.mark.asyncio
async def test_read_only_directories_do_not_count_as_a_directory_fault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three directories named *.json cannot be renamed (EACCES); a valid message still flows."""
    backend = FileQueueBackend(tmp_path, topic="rodirs")
    dirs = [f"{index:020d}-dir.json" for index in range(3)]
    for name in dirs:
        (tmp_path / "rodirs" / "incoming" / name).mkdir()
    await backend.publish({"id": "valid"})
    real_replace = os.replace

    def refuse_directories(src, dst):
        if Path(src).is_dir():
            raise PermissionError(errno.EACCES, "denied")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", refuse_directories)

    received = await _consume_all(backend)
    assert [m.payload for m in received] == [{"id": "valid"}]
    assert sorted(backend._refused_until) == dirs
    await backend.ack(received[0].id)

    # A fresh instance after a restart meets the same directories and still delivers.
    await backend.publish({"id": "later"})
    restarted = FileQueueBackend(tmp_path, topic="rodirs")
    assert [m.payload for m in await _consume_all(restarted)] == [{"id": "later"}]


@pytest.mark.asyncio
async def test_refused_entry_cools_down_for_exactly_sixty_seconds_without_blocking_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    now = [500.0]
    backend = FileQueueBackend(tmp_path, topic="cooldown", clock=lambda: now[0])
    stuck = "00000000000000000001-stuck.json"
    (tmp_path / "cooldown" / "incoming" / stuck).write_text('{"id":"stuck"}', encoding="utf-8")
    await backend.publish({"id": "first"})
    attempts = _refuse_replace(monkeypatch, name=stuck)

    with caplog.at_level(logging.ERROR):
        assert [m.payload for m in await _consume_all(backend)] == [{"id": "first"}]
    assert _claims(attempts) == 1
    assert caplog.text.count(f"Could not claim queue message {stuck!r}") == 1
    assert "Could not quarantine" not in caplog.text
    assert (tmp_path / "cooldown" / "incoming" / stuck).exists()

    await backend.publish({"id": "later"})
    now[0] = 559.9
    assert [m.payload for m in await _consume_all(backend)] == [{"id": "later"}]
    assert _claims(attempts) == 1

    now[0] = 560.0
    assert await _consume_all(backend) == []
    assert _claims(attempts) == 2

    monkeypatch.undo()
    now[0] = 620.0

    assert [m.payload for m in await _consume_all(backend)] == [{"id": "stuck"}]


@pytest.mark.asyncio
async def test_cooldown_uses_the_default_monotonic_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = FileQueueBackend(tmp_path, topic="realclock")
    stuck = "00000000000000000001-stuck.json"
    (tmp_path / "realclock" / "incoming" / stuck).write_text('{"id":"stuck"}', encoding="utf-8")
    attempts = _refuse_replace(monkeypatch, name=stuck)

    assert await _consume_all(backend) == []
    assert await _consume_all(backend) == []

    assert _claims(attempts) == 1


@pytest.mark.asyncio
async def test_name_leaving_incoming_is_not_held_in_cooldown_when_it_returns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "prune"
    backend = FileQueueBackend(tmp_path, topic="prune", clock=lambda: 1000.0)
    removed, renamed = (f"{index:020d}-p.json" for index in range(2))
    for name in (removed, renamed):
        (root / "incoming" / name).write_text('{"id":"x"}', encoding="utf-8")
    _refuse_replace(monkeypatch, error=errno.EISDIR, src_dir="incoming")
    assert await _consume_all(backend) == []

    (root / "incoming" / removed).unlink()
    (root / "incoming" / renamed).rename(root / "away.json")
    assert await _consume_all(backend) == []

    monkeypatch.undo()
    (root / "incoming" / removed).write_text('{"id":"x"}', encoding="utf-8")
    (root / "away.json").rename(root / "incoming" / renamed)

    received = await _consume_all(backend)

    assert sorted(m.id for m in received) == [removed, renamed]


@pytest.mark.asyncio
async def test_entry_taken_by_another_consumer_during_quarantine_is_not_cooled_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    backend = FileQueueBackend(tmp_path, topic="gone", clock=lambda: 1000.0)
    stuck = "00000000000000000001-gone.json"
    source = tmp_path / "gone" / "incoming" / stuck
    source.write_text('{"id":"gone"}', encoding="utf-8")
    await backend.publish({"id": "good"})
    real_replace = os.replace

    def replace_then_vanish(src, dst):
        if Path(src).name != stuck:
            return real_replace(src, dst)
        if Path(dst).parent.name == "processing":
            raise PermissionError(13, "denied")
        os.unlink(src)
        raise FileNotFoundError(2, "gone", str(src))

    monkeypatch.setattr(os, "replace", replace_then_vanish)

    with caplog.at_level(logging.ERROR):
        received = await _consume_all(backend)

    assert [m.payload for m in received] == [{"id": "good"}]
    assert "retrying after" not in caplog.text
    assert "Could not claim" not in caplog.text

    monkeypatch.undo()
    source.write_text('{"id":"gone-again"}', encoding="utf-8")

    assert [m.payload for m in await _consume_all(backend)] == [{"id": "gone-again"}]


@pytest.mark.asyncio
async def test_refusal_storm_log_is_bounded_per_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    now = [1000.0]
    backend = FileQueueBackend(tmp_path, topic="storm", clock=lambda: now[0])
    for index in range(300):
        (tmp_path / "storm" / "incoming" / f"{index:020d}-s.json").write_text(
            "{}", encoding="utf-8"
        )
    _refuse_replace(monkeypatch, error=errno.EISDIR, src_dir="incoming")

    with caplog.at_level(logging.ERROR):
        assert await _consume_all(backend) == []
        assert len(caplog.records) == 10
        assert "refusal(s) in the last" not in caplog.text

        assert await _consume_all(backend) == []
        assert len(caplog.records) == 10

        now[0] = 1061.0
        assert await _consume_all(backend) == []

    summary = "300 queue message refusal(s) in the last 60s; 290 per-name line(s) suppressed"
    assert caplog.text.count(summary) == 1
    assert caplog.text.count("Could not claim queue message") == 20
    assert len(caplog.records) == 21


@pytest.mark.asyncio
async def test_refusal_log_window_closes_exactly_at_sixty_seconds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    now = [1000.0]
    backend = FileQueueBackend(tmp_path, topic="window", clock=lambda: now[0])
    incoming = tmp_path / "window" / "incoming"
    _refuse_replace(monkeypatch, error=errno.EISDIR, src_dir="incoming")

    with caplog.at_level(logging.ERROR):
        for name, at in (("a", 1000.0), ("b", 1059.9)):
            now[0] = at
            (incoming / f"{name}.json").write_text("{}", encoding="utf-8")
            await _consume_all(backend)
        assert "refusal(s) in the last" not in caplog.text

        now[0] = 1060.0
        (incoming / "c.json").write_text("{}", encoding="utf-8")
        await _consume_all(backend)

    assert caplog.text.count("refusal(s) in the last 60s; 0 per-name line(s) suppressed") == 1


@pytest.mark.parametrize(
    ("name", "kept"),
    [
        ("a.json", "a.json"),
        ("x" * 255, "x" * 64),
        ("é" * 120, "é" * 32),
        ("€" * 85, "€" * 21),
        ("\U0001F600" * 63 + "ab", "\U0001F600" * 16),
        ("\udc80\udc81abc.json", "abc.json"),
    ],
)
def test_quarantine_name_is_bounded_character_aligned_and_unique(
    monkeypatch: pytest.MonkeyPatch, name: str, kept: str
) -> None:
    monkeypatch.setattr(time, "time_ns", lambda: 1234)

    first = file_backend_module._unique_quarantine_name(name)
    second = file_backend_module._unique_quarantine_name(name)

    assert first != second
    assert len(os.fsencode(first)) <= 255
    assert "/" not in first
    assert first.split("-", 2)[2] == kept


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["undecodable", "claim_refused"])
async def test_same_long_multibyte_name_quarantined_twice_keeps_both_copies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    backend = FileQueueBackend(tmp_path, topic="dlname")
    name = "\U0001F600" * 62 + ".json"
    entry = tmp_path / "dlname" / "incoming" / name
    if route == "claim_refused":
        _refuse_replace(monkeypatch, name=name, dst_dir="processing")

    for generation in ("first", "second"):
        entry.write_text(f"not json {generation}", encoding="utf-8")
        assert await _consume_all(backend) == []

    dead_letter = tmp_path / "dlname" / "dead-letter"
    assert all(len(os.fsencode(p.name)) <= 255 for p in dead_letter.iterdir())
    assert _dead_letter_originals(dead_letter) == ["\U0001F600" * 16] * 2
    contents = sorted(p.read_text(encoding="utf-8") for p in dead_letter.iterdir())
    assert contents == ["not json first", "not json second"]
