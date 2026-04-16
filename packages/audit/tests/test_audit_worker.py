"""Audit worker queue-backend selection tests."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("septum_queue")

from septum_queue import FileQueueBackend  # noqa: E402

from septum_audit.worker import _build_queue  # noqa: E402


def test_build_queue_prefers_file_dir_when_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("SEPTUM_QUEUE_URL", raising=False)
    monkeypatch.setenv("SEPTUM_QUEUE_DIR", str(tmp_path))
    queue = _build_queue("audit")
    assert isinstance(queue, FileQueueBackend)
    assert queue.topic == "audit"


def test_build_queue_missing_env_raises_systemexit(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("SEPTUM_QUEUE_URL", raising=False)
    monkeypatch.delenv("SEPTUM_QUEUE_DIR", raising=False)
    with pytest.raises(SystemExit, match="SEPTUM_QUEUE_URL"):
        _build_queue("audit")
