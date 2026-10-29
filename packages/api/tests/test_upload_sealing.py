"""Upload sealing (copy once, encrypt in place) and core-dump hardening."""

from __future__ import annotations

import os
import subprocess
import sys
import tracemalloc
from io import BytesIO
from pathlib import Path
from typing import List

import fitz
import pytest
from fastapi import HTTPException

from septum_api import process_hardening
from septum_api.routers import documents
from septum_api.utils.crypto import decrypt


def _pdf() -> bytes:
    """A one-page PDF."""

    doc = fitz.open()
    try:
        doc.new_page().insert_text((72, 72), "Sealed upload")
        return doc.tobytes()
    finally:
        doc.close()


def test_seal_upload_stores_the_encrypted_file_and_types_it(tmp_path: Path) -> None:
    payload = _pdf()
    source = BytesIO(payload)
    destination = tmp_path / "doc.enc"

    mime_type, file_format, size = documents._seal_upload(source, "application/octet-stream", destination)

    assert (mime_type, file_format, size) == ("application/pdf", "pdf", len(payload))
    assert source.closed
    assert decrypt(destination.read_bytes()) == payload


@pytest.mark.parametrize("payload, status", [(b"", 400), (b"x" * 2048, 413)])
def test_seal_upload_refuses_empty_and_oversize_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: bytes, status: int
) -> None:
    monkeypatch.setattr(documents, "MAX_UPLOAD_BYTES", 1024)
    destination = tmp_path / "doc.enc"

    with pytest.raises(HTTPException) as refused:
        documents._seal_upload(BytesIO(payload), None, destination)

    assert refused.value.status_code == status
    assert not destination.exists()


def test_seal_upload_unsupported_type_leaves_no_file(tmp_path: Path) -> None:
    """A type no ingester handles is refused before anything reaches the storage directory."""

    with pytest.raises(HTTPException) as refused:
        documents._seal_upload(BytesIO(b"MZ" + b"\x00" * 512), None, tmp_path / "doc.enc")

    assert refused.value.status_code == 400
    assert list(tmp_path.iterdir()) == []


def test_seal_upload_failed_write_leaves_no_partial_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A write that fails part-way is removed, so no truncated ciphertext stays on disk."""

    real_write_bytes = Path.write_bytes

    def failing_write_bytes(self: Path, data: bytes) -> int:
        real_write_bytes(self, bytes(data[:10]))
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "write_bytes", failing_write_bytes)

    with pytest.raises(OSError):
        documents._seal_upload(BytesIO(_pdf()), None, tmp_path / "doc.enc")

    assert list(tmp_path.iterdir()) == []


def test_seal_upload_sniffs_only_the_head(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: List[int] = []

    def fake_detect(head: bytes, fallback: str | None) -> str:
        seen.append(len(head))
        return "application/pdf"

    monkeypatch.setattr(documents, "_MIME_SNIFF_BYTES", 100)
    monkeypatch.setattr(documents, "_detect_mime_type", fake_detect)

    documents._seal_upload(BytesIO(b"%PDF-" + b"x" * 5000), None, tmp_path / "doc.enc")

    assert seen == [100]


def test_seal_upload_peaks_at_one_extra_copy(tmp_path: Path) -> None:
    """Beyond the parsed upload itself, sealing allocates one buffer of the file's size.

    The only other allocation is the bounded head copy handed to MIME detection.
    """

    payload = b"%PDF-" + os.urandom(32 * 1024 * 1024)
    source = BytesIO(payload)

    tracemalloc.start()
    try:
        documents._seal_upload(source, None, tmp_path / "doc.enc")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert peak < len(payload) + 2 * documents._MIME_SNIFF_BYTES


def test_disable_core_dumps_requests_a_zero_soft_and_hard_core_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """The call asks the OS for (0, 0) on RLIMIT_CORE, whatever limit the test process already has."""

    if process_hardening.resource is None:
        pytest.skip("resource module not available on this platform")
    calls: List[tuple] = []
    monkeypatch.setattr(process_hardening.resource, "setrlimit", lambda which, limits: calls.append((which, limits)))
    monkeypatch.setattr(process_hardening, "_mark_not_dumpable", lambda: True)

    assert process_hardening.disable_core_dumps() is True
    assert calls == [(process_hardening.resource.RLIMIT_CORE, (0, 0))]


def test_disable_core_dumps_zero_limit_is_inherited_by_children() -> None:
    if process_hardening.resource is None:
        pytest.skip("resource module not available on this platform")
    if process_hardening.resource.getrlimit(process_hardening.resource.RLIMIT_CORE)[1] == 0:
        pytest.skip("core hard limit is already 0 in this process, so a child proves nothing")
    code = (
        "import resource, subprocess\n"
        "from septum_api.process_hardening import disable_core_dumps\n"
        "assert disable_core_dumps()\n"
        "print(resource.getrlimit(resource.RLIMIT_CORE))\n"
        "print(subprocess.run(['sh', '-c', 'ulimit -Hc'], capture_output=True, text=True).stdout.strip())\n"
    )

    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)

    assert result.stdout.splitlines() == ["(0, 0)", "0"]


def test_disable_core_dumps_is_a_no_op_without_resource(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_hardening, "resource", None)

    assert process_hardening.disable_core_dumps() is False


@pytest.mark.asyncio
async def test_cancelled_seal_waits_for_the_thread_and_removes_its_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A request cancelled mid-seal leaves no ciphertext once the worker thread finishes writing."""

    import asyncio
    import threading

    started, proceed = threading.Event(), threading.Event()

    def slow_seal(source: BytesIO, fallback: str | None, destination: Path) -> tuple:
        started.set()
        proceed.wait(5)
        destination.write_bytes(b"ciphertext")
        return "application/pdf", "pdf", 10

    monkeypatch.setattr(documents, "_seal_upload", slow_seal)
    destination = tmp_path / "doc.enc"

    task = asyncio.create_task(documents._seal_upload_in_thread(BytesIO(b"x"), None, destination))
    await asyncio.to_thread(started.wait, 5)
    task.cancel()
    asyncio.get_running_loop().call_later(0.05, proceed.set)

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)

    assert proceed.is_set()
    assert not destination.exists()


def test_max_upload_bytes_is_parsed_and_range_checked() -> None:
    """MAX_UPLOAD_BYTES goes through env_int: below 1 or non-numeric is refused at import."""

    from septum_api.middleware import upload_limits

    assert upload_limits.MAX_UPLOAD_BYTES == 500 * 1024 * 1024
    code = "import septum_api.middleware.upload_limits"
    for value in ("0", "lots"):
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env={**os.environ, "MAX_UPLOAD_BYTES": value},
        )
        assert result.returncode != 0
        assert "EnvFlagError" in result.stderr
        assert "MAX_UPLOAD_BYTES" in result.stderr
