from __future__ import annotations

"""Regression tests for the hardening pass on tools.py."""

import threading
from pathlib import Path

import pytest

from septum_mcp import server as server_module
from septum_mcp.config import MCPConfig


def _stub_env(monkeypatch) -> None:
    monkeypatch.setenv("SEPTUM_REGULATIONS", "gdpr")
    monkeypatch.setenv("SEPTUM_USE_NER", "false")


def test_engine_holder_is_thread_safe(monkeypatch) -> None:
    """Concurrent first-call requests must not double-build the engine."""
    _stub_env(monkeypatch)
    holder = server_module._EngineHolder(MCPConfig.from_env())

    build_count = 0
    real_build = server_module._build_engine

    def counting_build(config):
        nonlocal build_count
        build_count += 1
        return real_build(config)

    monkeypatch.setattr(server_module, "_build_engine", counting_build)

    barrier = threading.Barrier(8)
    engines: list = []

    def worker() -> None:
        barrier.wait(timeout=2)
        engines.append(holder.get())

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert build_count == 1
    # Every thread saw the same engine instance.
    assert len({id(e) for e in engines}) == 1


def test_scan_file_rejects_symlink(tmp_path: Path, monkeypatch) -> None:
    """A planted symlink must be refused even if its target is readable."""
    _stub_env(monkeypatch)
    from septum_mcp import tools

    real_target = tmp_path / "secret.txt"
    real_target.write_text("nothing to see", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(real_target)

    holder = server_module._EngineHolder(MCPConfig.from_env())
    result = tools.scan_file(holder.get(), file_path=str(link))
    assert result["ok"] is False
    assert "symlink" in result["error"].lower()


def test_scan_file_respects_allow_list_root(
    tmp_path: Path, monkeypatch
) -> None:
    """Files outside SEPTUM_MCP_FILE_ROOT must be rejected."""
    _stub_env(monkeypatch)
    from septum_mcp import tools

    allowed = tmp_path / "allowed"
    allowed.mkdir()
    inside = allowed / "ok.txt"
    inside.write_text("data", encoding="utf-8")

    outside = tmp_path / "outside.txt"
    outside.write_text("data", encoding="utf-8")

    monkeypatch.setenv("SEPTUM_MCP_FILE_ROOT", str(allowed))
    holder = server_module._EngineHolder(MCPConfig.from_env())

    rejected = tools.scan_file(holder.get(), file_path=str(outside))
    assert rejected["ok"] is False
    assert "allow-listed root" in rejected["error"]

    # Sanity: the same engine still accepts files inside the root.
    accepted = tools.scan_file(holder.get(), file_path=str(inside))
    assert accepted["ok"] is True


def test_scan_file_rejects_oversized_file(tmp_path: Path, monkeypatch) -> None:
    _stub_env(monkeypatch)
    monkeypatch.setenv("SEPTUM_MCP_MAX_FILE_BYTES", "10")
    from septum_mcp import tools

    big = tmp_path / "big.txt"
    big.write_text("this string is more than ten bytes", encoding="utf-8")

    holder = server_module._EngineHolder(MCPConfig.from_env())
    result = tools.scan_file(holder.get(), file_path=str(big))
    assert result["ok"] is False
    assert "size cap" in result["error"]


def test_mask_text_failure_does_not_echo_input(monkeypatch, capsys) -> None:
    """A raised engine error must not flow back through stderr or envelope."""
    _stub_env(monkeypatch)
    from septum_mcp import tools

    class _BoomEngine:
        def mask(self, text, language="en"):
            raise ValueError(f"private_email={text}")

    sensitive = "user@example.com"
    result = tools.mask_text(_BoomEngine(), text=sensitive)
    assert result["ok"] is False
    assert sensitive not in result["error"]
    err = capsys.readouterr().err
    assert sensitive not in err
