"""AES-256-GCM based encryption utilities for Septum.

This module is responsible for managing the application-wide encryption key
and providing simple helpers to encrypt and decrypt data using AES-256-GCM.

Key management:
    - The key is loaded from bootstrap configuration (``config.json``) as a
      urlsafe base64-encoded 32-byte value.
    - If the key is missing, a new 256-bit key is generated on the first
      call to :func:`get_encryption_key` and cached in-process.

All functions are fully type-hinted and avoid logging any sensitive material.
"""

from __future__ import annotations

import base64
import hashlib
import os
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_ENCRYPTION_KEY: Optional[bytes] = None

NONCE_BYTES = 12
TAG_BYTES = 16


def generate_key() -> bytes:
    """Generate a new 256-bit AES key for use with AES-256-GCM."""
    return AESGCM.generate_key(bit_length=256)


def _load_key_from_config() -> Optional[bytes]:
    """Load the encryption key from bootstrap configuration.

    The value is expected to be urlsafe base64-encoded and must decode to
    exactly 32 bytes.
    """
    from ..bootstrap import get_config
    value = get_config().encryption_key
    if not value:
        return None

    try:
        key = base64.urlsafe_b64decode(value.encode("ascii"))
    except Exception as exc:  # pragma: no cover - defensive branch
        raise ValueError(
            "ENCRYPTION_KEY must be urlsafe base64-encoded."
        ) from exc

    if len(key) != 32:
        raise ValueError(
            "ENCRYPTION_KEY must decode to exactly 32 bytes for AES-256-GCM."
        )

    return key


def get_encryption_key() -> bytes:
    """Return the process-wide AES-256-GCM key, loading from bootstrap config.

    Bootstrap is responsible for persisting an ``encryption_key`` on first
    run (see ``bootstrap._ensure_secrets``); this function trusts that
    invariant. If the key is missing here, an ephemeral fallback would
    silently render every existing encrypted artifact (documents, anon
    maps) undecryptable on the next restart — fail loudly instead.
    """
    global _ENCRYPTION_KEY

    if _ENCRYPTION_KEY is not None:
        return _ENCRYPTION_KEY

    key = _load_key_from_config()
    if key is None:
        raise RuntimeError(
            "Encryption key missing from bootstrap config. "
            "Run setup or set ENCRYPTION_KEY before starting the API."
        )

    _ENCRYPTION_KEY = key
    return key


def encrypt(plaintext: bytes, associated_data: Optional[bytes] = None) -> bytes:
    """Encrypt ``plaintext`` using AES-256-GCM.

    The returned value contains the random 12-byte nonce prepended to the
    ciphertext+tag. This function does not perform any internal encoding;
    callers are expected to handle textual data encoding (e.g. UTF-8) before
    calling.
    """
    if not isinstance(plaintext, (bytes, bytearray)):
        raise TypeError("plaintext must be bytes or bytearray.")

    key = get_encryption_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, bytes(plaintext), associated_data)
    return nonce + ciphertext


def new_encrypt_buffer(plaintext_length: int) -> bytearray:
    """Return a zeroed buffer laid out as nonce || plaintext || tag space.

    Fill the plaintext region (see :func:`plaintext_region`) and pass the
    buffer to :func:`encrypt_buffer`, which encrypts it in place. Large
    payloads then never exist as a separate plaintext and ciphertext copy.
    """
    if plaintext_length < 0:
        raise ValueError("plaintext_length must not be negative.")
    return bytearray(NONCE_BYTES + plaintext_length + TAG_BYTES)


def plaintext_region(buffer: bytearray) -> memoryview:
    """Return a writable view of the plaintext region of a :func:`new_encrypt_buffer` buffer."""
    return memoryview(buffer)[NONCE_BYTES : len(buffer) - TAG_BYTES]


def encrypt_buffer(buffer: bytearray, associated_data: Optional[bytes] = None) -> bytearray:
    """Encrypt the plaintext region of ``buffer`` in place and return ``buffer``.

    The result has exactly the :func:`encrypt` format (12-byte nonce, then
    ciphertext+tag), so :func:`decrypt` reads it unchanged.
    """
    if not isinstance(buffer, bytearray) or len(buffer) < NONCE_BYTES + TAG_BYTES:
        raise TypeError("buffer must be a bytearray from new_encrypt_buffer().")

    aesgcm = AESGCM(get_encryption_key())
    nonce = os.urandom(NONCE_BYTES)
    view = memoryview(buffer)
    view[:NONCE_BYTES] = nonce
    aesgcm.encrypt_into(nonce, plaintext_region(buffer), associated_data, view[NONCE_BYTES:])
    return buffer


def decrypt(token: bytes, associated_data: Optional[bytes] = None) -> bytes:
    """Decrypt a value produced by :func:`encrypt`.

    ``token`` must contain the 12-byte nonce followed by the ciphertext+tag.
    """
    if not isinstance(token, (bytes, bytearray)):
        raise TypeError("token must be bytes or bytearray.")

    raw = bytes(token)
    if len(raw) <= 12:
        raise ValueError("token is too short to contain nonce and ciphertext.")

    key = get_encryption_key()
    aesgcm = AESGCM(key)
    nonce = raw[:12]
    return aesgcm.decrypt(nonce, memoryview(raw)[12:], associated_data)


def encrypt_to_base64(
    plaintext: bytes, associated_data: Optional[bytes] = None
) -> str:
    """Encrypt ``plaintext`` and return a urlsafe base64-encoded string."""
    token = encrypt(plaintext, associated_data)
    return base64.urlsafe_b64encode(token).decode("ascii")


def decrypt_from_base64(
    token_b64: str, associated_data: Optional[bytes] = None
) -> bytes:
    """Decrypt a value produced by :func:`encrypt_to_base64`."""
    try:
        token = base64.urlsafe_b64decode(token_b64.encode("ascii"))
    except Exception as exc:  # pragma: no cover - defensive branch
        raise ValueError("token_b64 must be valid urlsafe base64.") from exc

    return decrypt(token, associated_data)


def hash_text(text: str) -> str:
    """Return an HMAC-SHA-256 hex digest keyed by the encryption key.

    Used for non-reversible hashing of identifiers (e.g. IP, raw API
    keys) in logs. Plain SHA-256 is rainbow-tableable for low-entropy
    inputs (an IPv4 address is only 2^32 possibilities); HMAC under
    the per-instance encryption key forces an attacker to exfiltrate
    that key before any precomputation works. The key never leaves
    the host so log shippers carrying the digest cannot link two
    deployments' identifiers either.
    """
    import hmac

    key = get_encryption_key()
    return hmac.new(key, text.encode("utf-8"), hashlib.sha256).hexdigest()

