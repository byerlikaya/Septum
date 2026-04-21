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
    """Store the anonymization map in all three tiers."""
    # Tier 1: memory
    _document_maps[document_id] = anon_map

    serialized = _serialize(anon_map)

    # Tier 2: Redis (plaintext JSON, internal network only)
    await redis_set(_redis_key(document_id), serialized, ttl=_REDIS_TTL)

    # Tier 3: encrypted disk
    try:
        associated_data = str(document_id).encode("utf-8")
        encrypted_bytes = encrypt(serialized, associated_data=associated_data)
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

    # Tier 2: Redis
    redis_data = await redis_get(_redis_key(document_id))
    if redis_data is not None:
        try:
            anon_map = _deserialize(document_id, redis_data)
            _document_maps[document_id] = anon_map  # backfill memory
            return anon_map
        except (ValueError, KeyError, json.JSONDecodeError):
            logger.warning(
                "Corrupt Redis data for document_id=%s, falling through to disk",
                document_id,
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
        associated_data = str(document_id).encode("utf-8")
        plaintext = decrypt(encrypted_bytes, associated_data=associated_data)
        anon_map = _deserialize(document_id, plaintext)

        # Backfill memory and Redis
        _document_maps[document_id] = anon_map
        await redis_set(_redis_key(document_id), plaintext, ttl=_REDIS_TTL)

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
