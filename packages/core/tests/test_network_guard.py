"""The test-session network guard is active: no test reaches the Hub or the internet."""

from __future__ import annotations

import os
import socket

import pytest
from conftest import _blocked


def test_hub_is_offline_for_the_session() -> None:
    from huggingface_hub import constants

    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert constants.is_offline_mode() is True


def test_connect_to_a_public_address_is_refused() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        with pytest.raises(ConnectionRefusedError, match="real_network"):
            sock.connect(("203.0.113.1", 443))
    _blocked.remove(repr(("203.0.113.1", 443)))


def test_connect_to_loopback_is_allowed() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            client.connect(server.getsockname())
