from __future__ import annotations

"""Regression tests for the hardening pass on tools.py."""

import dataclasses
import os
import stat
import threading
from pathlib import Path

import pytest
from septum_mcp import server as server_module
from septum_mcp.config import MCPConfig
from septum_mcp.file_readers import file_identity


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
    result = tools.scan_file(holder.get(), file_root=None, file_path=str(link))
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

    pinned = tools.load_file_root(str(allowed))
    holder = server_module._EngineHolder(MCPConfig.from_env())

    rejected = tools.scan_file(holder.get(), file_root=pinned, file_path=str(outside))
    assert rejected["ok"] is False
    assert rejected["error"] == tools._SCAN_PATH_UNAVAILABLE

    # Sanity: the same engine still accepts files inside the root.
    accepted = tools.scan_file(holder.get(), file_root=pinned, file_path=str(inside))
    assert accepted["ok"] is True


def test_scan_file_rejects_oversized_file(tmp_path: Path, monkeypatch) -> None:
    _stub_env(monkeypatch)
    monkeypatch.setenv("SEPTUM_MCP_MAX_FILE_BYTES", "10")
    from septum_mcp import tools

    big = tmp_path / "big.txt"
    big.write_text("this string is more than ten bytes", encoding="utf-8")

    holder = server_module._EngineHolder(MCPConfig.from_env())
    result = tools.scan_file(holder.get(), file_root=None, file_path=str(big))
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


@pytest.mark.parametrize("relative", [".env", ".ssh/id_rsa", "notes/.git/config"])
def test_scan_file_rejects_dotfiles(tmp_path: Path, monkeypatch, relative: str) -> None:
    _stub_env(monkeypatch)
    from septum_mcp import tools

    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("API_KEY=jane@example.com", encoding="utf-8")

    holder = server_module._EngineHolder(MCPConfig.from_env())
    result = tools.scan_file(holder.get(), file_root=None, file_path=str(target))

    assert result["ok"] is False
    assert result["error"] == tools._SCAN_PATH_UNAVAILABLE
    assert "jane@example.com" not in result["error"]


def test_scan_file_root_may_live_under_a_dot_directory(tmp_path: Path, monkeypatch) -> None:
    _stub_env(monkeypatch)
    from septum_mcp import tools

    root = tmp_path / ".workspace"
    root.mkdir()
    inside = root / "ok.txt"
    inside.write_text("data", encoding="utf-8")
    pinned = tools.load_file_root(str(root))

    holder = server_module._EngineHolder(MCPConfig.from_env())
    assert tools.scan_file(holder.get(), file_root=pinned, file_path=str(inside))["ok"] is True
    (root / ".hidden").write_text("data", encoding="utf-8")
    assert tools.scan_file(holder.get(), file_root=pinned, file_path=str(root / ".hidden"))["ok"] is False


def test_scan_file_require_root_fails_closed(tmp_path: Path, monkeypatch) -> None:
    _stub_env(monkeypatch)
    from septum_mcp import tools

    target = tmp_path / "ok.txt"
    target.write_text("data", encoding="utf-8")
    holder = server_module._EngineHolder(MCPConfig.from_env())

    refused = tools.scan_file(holder.get(), file_root=None, file_path=str(target), require_root=True)
    assert refused["ok"] is False
    assert "SEPTUM_MCP_FILE_ROOT" in refused["error"]

    accepted = tools.scan_file(
        holder.get(),
        file_root=tools.load_file_root(str(tmp_path)),
        file_path=str(target),
        require_root=True,
    )
    assert accepted["ok"] is True


def _scan_root_fixture(tmp_path: Path, monkeypatch):
    _stub_env(monkeypatch)
    from septum_mcp import tools

    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("jane@example.com", encoding="utf-8")
    pinned = tools.load_file_root(str(root))
    engine = server_module._EngineHolder(MCPConfig.from_env()).get()
    return tools, engine, root, outside, secret, pinned


def test_scan_file_outside_root_hides_existence(tmp_path: Path, monkeypatch) -> None:
    """Missing and existing files outside the root must be indistinguishable."""
    tools, engine, root, outside, secret, pinned = _scan_root_fixture(tmp_path, monkeypatch)

    existing = tools.scan_file(engine, file_root=pinned, file_path=str(secret))
    missing = tools.scan_file(engine, file_root=pinned, file_path=str(outside / "nope.txt"))
    traversal = tools.scan_file(engine, file_root=pinned, file_path=str(root / ".." / "outside" / "secret.txt"))
    missing_inside = tools.scan_file(engine, file_root=pinned, file_path=str(root / "nope.txt"))

    results = [existing, missing, traversal, missing_inside]
    assert all(r["ok"] is False for r in results)
    assert len({r["error"] for r in results}) == 1
    for r in results:
        assert str(root) not in r["error"]
        assert str(outside) not in r["error"]
        assert "secret.txt" not in r["error"]


def test_scan_file_refuses_symlink_escaping_root(tmp_path: Path, monkeypatch) -> None:
    tools, engine, root, outside, secret, pinned = _scan_root_fixture(tmp_path, monkeypatch)
    (root / "link.txt").symlink_to(secret)
    (root / "linked_dir").symlink_to(outside, target_is_directory=True)

    file_link = tools.scan_file(engine, file_root=pinned, file_path=str(root / "link.txt"))
    dir_link = tools.scan_file(engine, file_root=pinned, file_path=str(root / "linked_dir" / "secret.txt"))

    assert file_link["ok"] is False
    assert dir_link["ok"] is False
    assert dir_link["error"] == tools._SCAN_PATH_UNAVAILABLE
    for r in (file_link, dir_link):
        assert str(root) not in r["error"]
        assert "jane@example.com" not in r["error"]


def test_scan_file_accepts_root_configured_behind_symlink(tmp_path: Path, monkeypatch) -> None:
    _stub_env(monkeypatch)
    from septum_mcp import tools

    real_root = tmp_path / "real"
    real_root.mkdir()
    (real_root / "ok.txt").write_text("data", encoding="utf-8")
    alias = tmp_path / "alias"
    alias.symlink_to(real_root, target_is_directory=True)
    pinned = tools.load_file_root(str(alias))
    engine = server_module._EngineHolder(MCPConfig.from_env()).get()

    assert tools.scan_file(engine, file_root=pinned, file_path=str(alias / "ok.txt"))["ok"] is True
    assert tools.scan_file(engine, file_root=pinned, file_path=str(real_root / "ok.txt"))["ok"] is True


def test_scan_file_symlink_then_dotdot_cannot_escape_root(tmp_path: Path, monkeypatch) -> None:
    """``root/link/../x`` must be judged by where the kernel resolves it, not by its spelling."""
    tools, engine, root, outside, secret, pinned = _scan_root_fixture(tmp_path, monkeypatch)
    inner = outside / "inner"
    inner.mkdir()
    (root / "link").symlink_to(inner, target_is_directory=True)
    (root / "secret.txt").write_text("decoy only", encoding="utf-8")

    result = tools.scan_file(engine, file_root=pinned, file_path=str(root / "link" / ".." / "secret.txt"), mask=True)

    if result["ok"]:
        assert "jane@example.com" not in result["data"]["masked_text"]
        assert result["data"]["entity_count"] == 0
    else:
        assert result["error"] == tools._SCAN_PATH_UNAVAILABLE
        assert str(root) not in result["error"]
        assert str(outside) not in result["error"]


def test_validated_target_is_the_resolved_path(tmp_path: Path, monkeypatch) -> None:
    tools, engine, root, outside, secret, pinned = _scan_root_fixture(tmp_path, monkeypatch)
    sub = root / "sub"
    sub.mkdir()
    (sub / "ok.txt").write_text("data", encoding="utf-8")

    error, target = tools._validate_scan_path(
        str(root / "sub" / ".." / "sub" / "ok.txt"), require_root=True, file_root=pinned
    )

    assert error is None
    assert target.path == (sub / "ok.txt").resolve()


def test_scan_file_size_cap_applies_to_the_file_actually_read(tmp_path: Path, monkeypatch) -> None:
    """A file that grows past the cap after validation is still refused."""
    tools, engine, root, outside, secret, pinned = _scan_root_fixture(tmp_path, monkeypatch)
    monkeypatch.setenv("SEPTUM_MCP_MAX_FILE_BYTES", "64")
    target = root / "grows.txt"
    target.write_text("small", encoding="utf-8")

    real_validate = tools._validate_scan_path

    def validate_then_grow(*args, **kwargs):
        outcome = real_validate(*args, **kwargs)
        target.write_text("x" * 1024, encoding="utf-8")
        return outcome

    monkeypatch.setattr(tools, "_validate_scan_path", validate_then_grow)
    result = tools.scan_file(engine, file_root=pinned, file_path=str(target))

    assert result["ok"] is False
    assert "size cap" in result["error"]
    assert str(root) not in result["error"]


def test_scan_file_refuses_file_swapped_after_validation(tmp_path: Path, monkeypatch) -> None:
    tools, engine, root, outside, secret, pinned = _scan_root_fixture(tmp_path, monkeypatch)
    target = root / "swap.txt"
    target.write_text("decoy", encoding="utf-8")

    real_validate = tools._validate_scan_path

    def validate_then_swap(*args, **kwargs):
        outcome = real_validate(*args, **kwargs)
        target.unlink()
        target.write_text("jane@example.com", encoding="utf-8")
        return outcome

    monkeypatch.setattr(tools, "_validate_scan_path", validate_then_swap)
    result = tools.scan_file(engine, file_root=pinned, file_path=str(target), mask=True)

    assert result["ok"] is False
    assert result["error"] == tools._SCAN_PATH_UNAVAILABLE


class _ReusedInodeStat:
    """An ``fstat`` result for a replacement that reused the inode number."""

    def __init__(self, real: os.stat_result, size_delta: int, ctime_delta: int) -> None:
        self._real = real
        self.st_size = real.st_size + size_delta
        self.st_ctime_ns = real.st_ctime_ns + ctime_delta

    def __getattr__(self, name: str):
        return getattr(self._real, name)


@pytest.mark.parametrize(
    ("size_delta", "ctime_delta"),
    [(0, 1), (1, 0)],
    ids=["ctime-changed", "size-changed"],
)
def test_scan_file_refuses_replacement_that_reuses_the_inode(
    tmp_path: Path, monkeypatch, size_delta: int, ctime_delta: int
) -> None:
    """Same ``(st_dev, st_ino)`` is not enough: ext4 reuses a freed inode at once."""
    tools, engine, root, outside, secret, pinned = _scan_root_fixture(tmp_path, monkeypatch)
    target = root / "reused.txt"
    target.write_text("jane@example.com", encoding="utf-8")

    from septum_mcp import file_readers

    real_fstat = os.fstat

    def reused_inode_fstat(fd: int):
        info = real_fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return info
        return _ReusedInodeStat(info, size_delta, ctime_delta)

    monkeypatch.setattr(file_readers.os, "fstat", reused_inode_fstat)
    result = tools.scan_file(engine, file_root=pinned, file_path=str(target), mask=True)

    assert result["ok"] is False
    assert result["error"] == tools._SCAN_PATH_UNAVAILABLE


def test_scan_file_reads_untouched_file_with_matching_identity(
    tmp_path: Path, monkeypatch
) -> None:
    tools, engine, root, outside, secret, pinned = _scan_root_fixture(tmp_path, monkeypatch)
    target = root / "steady.txt"
    target.write_text("jane@example.com", encoding="utf-8")

    error, validated = tools._validate_scan_path(
        str(target), require_root=True, file_root=pinned
    )
    assert error is None
    assert validated.identity == file_identity(target.stat())

    result = tools.scan_file(engine, file_root=pinned, file_path=str(target), mask=True)

    assert result["ok"] is True


def test_scan_file_read_error_is_generic(tmp_path: Path, monkeypatch, caplog) -> None:
    tools, engine, root, outside, secret, pinned = _scan_root_fixture(tmp_path, monkeypatch)
    broken = root / "broken.pdf"
    broken.write_bytes(b"definitely not a pdf")

    with caplog.at_level("WARNING"):
        result = tools.scan_file(engine, file_root=pinned, file_path=str(broken))

    assert result["ok"] is False
    assert result["error"] == tools._SCAN_READ_FAILED
    assert str(root) not in caplog.text
    assert "broken.pdf" not in caplog.text


def test_scan_file_empty_error_does_not_echo_path(tmp_path: Path, monkeypatch) -> None:
    tools, engine, root, outside, secret, pinned = _scan_root_fixture(tmp_path, monkeypatch)
    empty = root / "empty.txt"
    empty.write_text("", encoding="utf-8")

    result = tools.scan_file(engine, file_root=pinned, file_path=str(empty))

    assert result["ok"] is False
    assert "empty" in result["error"]
    assert str(root) not in result["error"]


def test_scan_file_reads_nested_file_inside_root(tmp_path: Path, monkeypatch) -> None:
    tools, engine, root, outside, secret, pinned = _scan_root_fixture(tmp_path, monkeypatch)
    (root / "a" / "b").mkdir(parents=True)
    (root / "a" / "b" / "c.txt").write_text("plain words", encoding="utf-8")

    result = tools.scan_file(engine, file_root=pinned, file_path=str(root / "a" / "b" / "c.txt"))

    assert result["ok"] is True


def test_scan_file_refuses_directory_swapped_for_symlink_after_validation(
    tmp_path: Path, monkeypatch
) -> None:
    """A directory swapped for a symlink between resolve and stat cannot redirect the read.

    The worst interleaving lets validation record the outside file's identity
    (resolve sees the real directory, stat already sees the symlink), so the
    identity check alone would pass; only the descriptor walk refuses it.
    """
    tools, engine, root, outside, secret, pinned = _scan_root_fixture(tmp_path, monkeypatch)
    inside = root / "d"
    inside.mkdir()
    (inside / "x.txt").write_text("decoy", encoding="utf-8")
    escaped = outside / "d"
    escaped.mkdir()
    (escaped / "x.txt").write_text("jane@example.com", encoding="utf-8")

    error, target = tools._validate_scan_path(
        str(inside / "x.txt"), require_root=True, file_root=pinned
    )
    assert error is None
    inside.rename(root / "parked")
    inside.symlink_to(escaped, target_is_directory=True)
    raced = dataclasses.replace(target, identity=file_identity((escaped / "x.txt").stat()))

    result = tools._read_scan_target(raced)

    assert result.ok is False
    assert "jane@example.com" not in result.text


def test_scan_file_refuses_hardlink(tmp_path: Path, monkeypatch) -> None:
    tools, engine, root, outside, secret, pinned = _scan_root_fixture(tmp_path, monkeypatch)
    try:
        os.link(secret, root / "alias.txt")
    except OSError:
        pytest.skip("hardlinks across these directories are not supported here")

    result = tools.scan_file(engine, file_root=pinned, file_path=str(root / "alias.txt"), mask=True)

    assert result["ok"] is False
    assert result["error"] == tools._SCAN_PATH_UNAVAILABLE


def test_scan_file_refuses_fifo_and_device(tmp_path: Path, monkeypatch) -> None:
    tools, engine, root, outside, secret, pinned = _scan_root_fixture(tmp_path, monkeypatch)
    os.mkfifo(root / "pipe.txt")

    fifo = tools.scan_file(engine, file_root=pinned, file_path=str(root / "pipe.txt"))
    device = tools.scan_file(engine, file_root=None, file_path="/dev/null")

    assert fifo["ok"] is False
    assert fifo["error"] == tools._SCAN_PATH_UNAVAILABLE
    assert device["ok"] is False
