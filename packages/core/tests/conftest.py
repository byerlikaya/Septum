"""Test-session guard: no test reaches the Hugging Face Hub or any non-local host.

``HF_HUB_OFFLINE`` is set before anything imports ``huggingface_hub`` (the hub
reads it once, at import), and every socket connect to a non-loopback address
fails loudly. A test that really needs the network opts in with
``@pytest.mark.real_network``.
"""

from __future__ import annotations

import ipaddress
import os
import socket

import pytest

_SESSION_OFFLINE = os.environ.get("HF_HUB_OFFLINE")
os.environ.setdefault("HF_HUB_OFFLINE", "1")

_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex


_blocked: list[str] = []


class NetworkBlocked(ConnectionRefusedError):
    """A test tried to reach a non-loopback host.

    A connection error, so a library with an offline fallback degrades the
    way it would on an air-gapped host; every attempt is still listed in the
    terminal summary.
    """


def _is_local(sock: socket.socket, address: object) -> bool:
    if sock.family == getattr(socket, "AF_UNIX", None):
        return True
    host = address[0] if isinstance(address, tuple) else address
    try:
        return ipaddress.ip_address(str(host).split("%")[0]).is_loopback
    except ValueError:
        return host == "localhost"


def _guarded(real):
    def connect(self: socket.socket, address: object):
        if not _is_local(self, address):
            _blocked.append(repr(address))
            raise NetworkBlocked(
                f"test tried to reach {address!r}; mark it @pytest.mark.real_network"
            )
        return real(self, address)

    return connect


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "real_network: the test may reach the internet (real model downloads)"
    )


def pytest_terminal_summary(terminalreporter) -> None:
    if _blocked:
        terminalreporter.write_sep("-", "network access blocked by the test guard")
        for address in sorted(set(_blocked)):
            terminalreporter.write_line(f"{address} x{_blocked.count(address)}")


@pytest.fixture(autouse=True)
def _no_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.node.get_closest_marker("real_network"):
        monkeypatch.setenv("HF_HUB_OFFLINE", _SESSION_OFFLINE or "0")
        return
    monkeypatch.setattr(socket.socket, "connect", _guarded(_real_connect))
    monkeypatch.setattr(socket.socket, "connect_ex", _guarded(_real_connect_ex))
