"""Per-document anonymization map store with 3-tier caching.

Tier 1: In-memory dict (fastest, single-worker)
Tier 2: Redis (shared across workers, 24h TTL)
Tier 3: Encrypted disk (persistent across restarts, AES-256-GCM)

Maps are populated at document upload and used at chat time for
de-anonymizing LLM responses. They are never sent to the frontend,
cloud, or logs. When a document is deleted, its map is removed from
all tiers.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Dict

from ..utils.crypto import decrypt, encrypt
from .anonymization_map import AnonymizationMap
from .redis_client import redis_delete, redis_get, redis_set

logger = logging.getLogger(__name__)

_document_maps: Dict[int, AnonymizationMap] = {}

_ANON_MAP_DIR = Path(
    os.getenv("ANON_MAP_STORAGE_DIR", "./anon_maps")
).resolve()

_REDIS_TTL = 86400  # 24 hours


def _map_path(document_id: int) -> Path:
    """Path to the encrypted map file for a document."""
    _ANON_MAP_DIR.mkdir(parents=True, exist_ok=True)
    return _ANON_MAP_DIR / f"{document_id}.enc"


def _redis_key(document_id: int) -> str:
    return f"anon_map:{document_id}"


def _serialize(anon_map: AnonymizationMap) -> bytes:
    """Serialize anonymization map fields."""
    payload = {
        "entity_map": anon_map.entity_map,
        "language": anon_map.language,
        "token_to_placeholder": anon_map.token_to_placeholder,
        "token_counter": anon_map.token_counter,
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _deserialize(document_id: int, data: bytes) -> AnonymizationMap:
    """Deserialize payload into an AnonymizationMap."""
    payload = json.loads(data.decode("utf-8"))
    return AnonymizationMap(
        document_id=document_id,
        language=payload.get("language", "en"),
        entity_map=payload.get("entity_map", {}),
        token_to_placeholder=payload.get("token_to_placeholder", {}),
        token_counter=payload.get("token_counter", {}),
    )


async def set_document_map(document_id: int, anon_map: AnonymizationMap) -> None:
    """Store the anonymization map in all three tiers.

    All tiers carry the SAME AES-256-GCM ciphertext. Redis used to hold
    plaintext JSON which exposed every original PII value to anyone with
    Redis read access (RDB/AOF backups, MITM on cleartext links,
    compromised infra). The disk-tier ciphertext is reused so an
    operator never accidentally writes raw PII to either persistence
    layer.
    """
    # Tier 1: memory (plaintext, in-process only)
    _document_maps[document_id] = anon_map

    serialized = _serialize(anon_map)
    associated_data = str(document_id).encode("utf-8")
    encrypted_bytes = encrypt(serialized, associated_data=associated_data)

    # Tier 2: Redis (encrypted)
    await redis_set(_redis_key(document_id), encrypted_bytes, ttl=_REDIS_TTL)

    # Tier 3: encrypted disk
    try:
        _map_path(document_id).write_bytes(encrypted_bytes)
    except OSError:
        pass


async def get_document_map(document_id: int) -> AnonymizationMap | None:
    """Return the anonymization map, checking tiers in order.

    Tier 1 (memory) → Tier 2 (Redis) → Tier 3 (encrypted disk).
    On cache miss, backfills higher tiers.
    """
    # Tier 1: memory
    if document_id in _document_maps:
        return _document_maps[document_id]

    associated_data = str(document_id).encode("utf-8")

    # Tier 2: Redis (encrypted)
    redis_data = await redis_get(_redis_key(document_id))
    if redis_data is not None:
        try:
            plaintext = decrypt(redis_data, associated_data=associated_data)
            anon_map = _deserialize(document_id, plaintext)
            _document_maps[document_id] = anon_map  # backfill memory
            return anon_map
        except Exception as exc:
            logger.warning(
                "Could not decrypt Redis anon map for document_id=%s "
                "(falling through to disk): %s",
                document_id,
                type(exc).__name__,
            )
            await redis_delete(_redis_key(document_id))

    # Tier 3: encrypted disk
    path = _map_path(document_id)
    if not path.exists():
        logger.debug(
            "No anonymization map file for document_id=%s at %s",
            document_id,
            path,
        )
        return None
    try:
        encrypted_bytes = path.read_bytes()
        plaintext = decrypt(encrypted_bytes, associated_data=associated_data)
        anon_map = _deserialize(document_id, plaintext)

        # Backfill memory and Redis (Redis backfill carries ciphertext,
        # not plaintext, so the bridge tier never holds raw originals).
        _document_maps[document_id] = anon_map
        await redis_set(_redis_key(document_id), encrypted_bytes, ttl=_REDIS_TTL)

        return anon_map
    except Exception as e:
        logger.warning(
            "Could not load anonymization map for document_id=%s (deanonymization will be skipped): %s",
            document_id,
            type(e).__name__,
        )
        return None


async def pop_document_map(document_id: int) -> None:
    """Remove the map from all tiers."""
    # Tier 1: memory
    _document_maps.pop(document_id, None)

    # Tier 2: Redis
    await redis_delete(_redis_key(document_id))

    # Tier 3: disk
    path = _map_path(document_id)
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass
