"""The Linux non-dumpable flag set alongside the zero core-dump limit."""

from __future__ import annotations

import ctypes
import subprocess
import sys
from pathlib import Path

import pytest

from septum_api import process_hardening


class _FakePrctl:
    """Records the calls and the ctypes signature set on it."""

    def __init__(self, result: int) -> None:
        self.result = result
        self.calls: list[tuple] = []
        self.argtypes: list | None = None
        self.restype: object = None

    def __call__(self, *args: int) -> int:
        self.calls.append(args)
        return self.result


class _FakeLibc:
    def __init__(self, prctl: _FakePrctl | None) -> None:
        if prctl is not None:
            self.prctl = prctl


def test_not_dumpable_is_skipped_off_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_hardening.sys, "platform", "darwin")

    assert process_hardening._mark_not_dumpable() is False


def test_linux_libc_without_prctl_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_hardening.sys, "platform", "linux")
    monkeypatch.setattr(process_hardening.ctypes, "CDLL", lambda *args, **kwargs: _FakeLibc(None))

    with pytest.raises(OSError):
        process_hardening._mark_not_dumpable()


def test_prctl_gets_an_explicit_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    prctl = _FakePrctl(0)
    monkeypatch.setattr(process_hardening.sys, "platform", "linux")
    monkeypatch.setattr(process_hardening.ctypes, "CDLL", lambda *args, **kwargs: _FakeLibc(prctl))

    assert process_hardening._mark_not_dumpable() is True
    assert prctl.argtypes == [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    assert prctl.restype is ctypes.c_int
    assert prctl.calls == [(4, 0, 0, 0, 0)]


def test_failing_prctl_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_hardening.sys, "platform", "linux")
    monkeypatch.setattr(process_hardening.ctypes, "CDLL", lambda *args, **kwargs: _FakeLibc(_FakePrctl(-1)))
    monkeypatch.setattr(process_hardening.ctypes, "get_errno", lambda: 1)

    with pytest.raises(OSError):
        process_hardening._mark_not_dumpable()


def test_disable_core_dumps_marks_the_process_not_dumpable_on_linux() -> None:
    if not sys.platform.startswith("linux"):
        pytest.skip("prctl is Linux-only")
    code = (
        "import ctypes\n"
        "from septum_api.process_hardening import disable_core_dumps\n"
        "disable_core_dumps()\n"
        "print(ctypes.CDLL(None).prctl(3, 0, 0, 0, 0))\n"
    )

    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)

    assert result.stdout.strip() == "0"


def test_main_hardens_before_bootstrap_import() -> None:
    """main.py disables core dumps before it imports the bootstrap module."""

    source = Path(process_hardening.__file__).with_name("main.py").read_text()

    assert source.index("disable_core_dumps()") < source.index("from . import bootstrap")


def test_ocr_worker_pool_hardens_each_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    """The spawned OCR worker re-applies the hardening, since exec drops the dumpable flag."""

    import pickle

    from septum_api.services.ingestion.ocr import paddle_provider

    created: list[dict] = []

    class _RecordingPool:
        def __init__(self, **kwargs: object) -> None:
            created.append(kwargs)

    monkeypatch.setattr(paddle_provider, "_ocr_pool", None)
    monkeypatch.setattr(paddle_provider, "ProcessPoolExecutor", _RecordingPool)

    paddle_provider._get_ocr_pool()

    initializer = created[0]["initializer"]
    assert initializer is process_hardening.disable_core_dumps
    assert pickle.loads(pickle.dumps(initializer)) is initializer
