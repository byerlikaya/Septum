"""Signed fingerprint of the placeholder numbering a chat turn was built on.

A turn unifies the query map and every document map into one placeholder
space. A ``pre_approved_chunks`` retry rebuilds that space from the maps as
they are *now*; if a map, the document set or the query map changed in the
meantime, ``[PERSON_NAME_3]`` would resolve to a different person. The
original turn therefore hands the client a token that commits to every
input of the numbering, and the retry is refused unless the rebuilt inputs
produce the same token.

The token is an HMAC under a key derived from the server's JWT secret and
bound to the user id, so it can be neither forged nor replayed by another
user. It carries no data of its own; the fingerprint it commits to is a
digest that includes original values, which is why only its keyed MAC ever
leaves the server.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Mapping, Optional

from ..bootstrap import get_config
from .anonymization_map import AnonymizationMap

_TOKEN_VERSION = "v1"
# The algorithm that turns the inputs into a numbering. A change to it
# renumbers the same inputs, so a token issued under an older one must fail.
NUMBERING_ALGORITHM = "unify-v2"
_KEY_LABEL = b"retry-numbering-token/v1"
MAX_NUMBERING_TOKEN_LENGTH = 128


def _map_digest(anon_map: AnonymizationMap) -> str:
    """Digest every map field the unified numbering is derived from.

    ``entity_map`` keeps insertion order because the numbering is assigned
    in that order.
    """
    payload = json.dumps(
        {
            "language": anon_map.language,
            "entities": list(anon_map.entity_map.items()),
            "tokens": sorted(anon_map.token_to_placeholder.items()),
        },
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def numbering_fingerprint(
    query_map: AnonymizationMap,
    document_maps: Mapping[int, AnonymizationMap],
    seed_document_id: Optional[int],
) -> bytes:
    """Return the digest of every input of a turn's unified numbering.

    ``seed_document_id`` is the document whose map seeded the query map
    (the lead document of a manual turn, ``None`` in auto mode). The
    numbering algorithm is part of the digest (``NUMBERING_ALGORITHM``).
    """
    payload = json.dumps(
        {
            "algorithm": NUMBERING_ALGORITHM,
            "documents": sorted(document_maps),
            "seed": seed_document_id,
            "query": _map_digest(query_map),
            "maps": {str(d): _map_digest(document_maps[d]) for d in sorted(document_maps)},
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).digest()


def _signing_key() -> bytes:
    secret = get_config().jwt_secret_key
    return hmac.new(secret.encode("utf-8"), _KEY_LABEL, hashlib.sha256).digest()


def sign_numbering(fingerprint: bytes, user_id: int) -> str:
    """Return the token that commits ``user_id`` to ``fingerprint``."""
    message = f"{_TOKEN_VERSION}|{user_id}|".encode("utf-8") + fingerprint
    mac = hmac.new(_signing_key(), message, hashlib.sha256).digest()
    return f"{_TOKEN_VERSION}.{base64.urlsafe_b64encode(mac).decode('ascii').rstrip('=')}"


def verify_numbering(token: Optional[str], fingerprint: bytes, user_id: int) -> bool:
    """Return True only if ``token`` was issued to ``user_id`` for ``fingerprint``.

    Issued tokens are short ASCII strings, so an over-long token or one
    that is not ASCII (including lone surrogates) is invalid rather than
    an encoding error.
    """
    if not token or len(token) > MAX_NUMBERING_TOKEN_LENGTH:
        return False
    try:
        presented = token.encode("ascii")
    except UnicodeEncodeError:
        return False
    expected = sign_numbering(fingerprint, user_id)
    return hmac.compare_digest(presented, expected.encode("ascii"))


__all__ = [
    "MAX_NUMBERING_TOKEN_LENGTH",
    "NUMBERING_ALGORITHM",
    "numbering_fingerprint",
    "sign_numbering",
    "verify_numbering",
]
