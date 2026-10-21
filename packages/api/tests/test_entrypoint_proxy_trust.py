"""Container entrypoint: FORWARDED_ALLOW_IPS must name proxies, never everyone."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ENTRYPOINT = Path(__file__).resolve().parents[1] / "scripts" / "docker-entrypoint.sh"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")


def _run(tmp_path: Path, forwarded_allow_ips: str | None) -> tuple[subprocess.CompletedProcess, list[str]]:
    """Run the entrypoint with a stub ``python`` that records each call's argv.

    The proxy-trust check is handed to the real interpreter, so the test
    exercises the actual parsing.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.txt"
    stub = bin_dir / "python"
    stub.write_text(
        "#!/bin/sh\n"
        f'case "$*" in *ip_network*) exec "{sys.executable}" "$@";; esac\n'
        f'printf "%s\\n" "$*" >> "{calls}"\n'
    )
    stub.chmod(0o755)
    env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "HOME": str(tmp_path)}
    if forwarded_allow_ips is not None:
        env["FORWARDED_ALLOW_IPS"] = forwarded_allow_ips
    result = subprocess.run(
        [BASH, str(ENTRYPOINT)], env=env, capture_output=True, text=True, timeout=30
    )
    return result, calls.read_text().splitlines() if calls.exists() else []


def _uvicorn_call(calls: list[str]) -> str | None:
    return next((call for call in calls if "uvicorn" in call), None)


@pytest.mark.parametrize(
    "value",
    [
        "*",
        "0.0.0.0/0",
        "::/0",
        "127.0.0.1,*",
        "127.0.0.1, 0.0.0.0/0",
        "172.18.0.0/16 ::/0",
        "0.0.0.0/00",
        "0.0.0.0/000",
        "0.0.0.0/0.0.0.0",
        "::/00",
        "10.0.0.0/7",
        "2001:db8::/31",
    ],
)
def test_wildcard_or_catch_all_range_is_refused(tmp_path: Path, value: str):
    result, calls = _run(tmp_path, value)
    assert result.returncode == 1
    assert "FORWARDED_ALLOW_IPS" in result.stderr
    assert _uvicorn_call(calls) is None


@pytest.mark.parametrize(
    "value",
    ["127.0.0.1", "172.18.0.5", "172.18.0.0/16", "127.0.0.1, 172.18.0.5", "10.0.0.0/8", "2001:db8::/32"],
)
def test_proxy_addresses_start_uvicorn_trusting_only_them(tmp_path: Path, value: str):
    result, calls = _run(tmp_path, value)
    assert result.returncode == 0, result.stderr
    call = _uvicorn_call(calls)
    assert call is not None
    assert "--proxy-headers" in call
    assert call.endswith(f"--forwarded-allow-ips {value}")


def test_default_trusts_loopback_only(tmp_path: Path):
    result, calls = _run(tmp_path, None)
    assert result.returncode == 0, result.stderr
    assert _uvicorn_call(calls).endswith("--forwarded-allow-ips 127.0.0.1")
