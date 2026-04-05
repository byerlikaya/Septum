"""Tests for the AES-256-GCM crypto utilities."""

from __future__ import annotations

import os

import pytest
from cryptography.exceptions import InvalidTag

from backend.app.utils import crypto


def _reset_crypto_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear in-process key cache and environment for a clean test state."""
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    # Access the private cache in a controlled test-only manner.
    setattr(crypto, "_ENCRYPTION_KEY", None)


def test_generate_key_length() -> None:
    key = crypto.generate_key()
    assert isinstance(key, bytes)
    assert len(key) == 32


def test_get_encryption_key_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_crypto_state(monkeypatch)

    key1 = crypto.get_encryption_key()
    key2 = crypto.get_encryption_key()

    assert key1 == key2
    assert len(key1) == 32


def test_encrypt_decrypt_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_crypto_state(monkeypatch)

    plaintext = b"septum-secret-data"
    token = crypto.encrypt(plaintext)
    decrypted = crypto.decrypt(token)

    assert decrypted == plaintext


def test_encrypt_uses_random_nonce(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_crypto_state(monkeypatch)

    plaintext = b"same-plaintext"
    token1 = crypto.encrypt(plaintext)
    token2 = crypto.encrypt(plaintext)

    # With a random nonce, encrypting the same plaintext twice should yield
    # different ciphertext values with overwhelming probability.
    assert token1 != token2


def test_associated_data_is_authenticated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset_crypto_state(monkeypatch)

    plaintext = b"authenticated-data"
    associated_data = b"context"

    token = crypto.encrypt(plaintext, associated_data=associated_data)
    # Decryption with the same associated data should succeed.
    decrypted = crypto.decrypt(token, associated_data=associated_data)
    assert decrypted == plaintext

    # Decryption with different associated data must fail.
    with pytest.raises(InvalidTag):
        crypto.decrypt(token, associated_data=b"different-context")


def test_base64_helpers_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_crypto_state(monkeypatch)

    plaintext = b"base64-encoded-secret"
    token_b64 = crypto.encrypt_to_base64(plaintext)
    decrypted = crypto.decrypt_from_base64(token_b64)

    assert decrypted == plaintext

