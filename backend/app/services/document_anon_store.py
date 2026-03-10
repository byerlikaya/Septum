"""Per-document anonymization map store with optional encrypted persistence.

Maps are populated at document upload and used at chat time for
de-anonymizing LLM responses. They are kept in memory and optionally
persisted encrypted on disk (AES-256-GCM) so that:
- Deanonymization works after server restart.
- Deanonymization works with multiple workers (all read the same encrypted file).

Maps are never sent to the frontend, cloud, or logs. When a document
is deleted, its map is removed from memory and disk.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Dict

from ..utils.crypto import decrypt, encrypt
from .anonymization_map import AnonymizationMap

logger = logging.getLogger(__name__)

_document_maps: Dict[int, AnonymizationMap] = {}

_ANON_MAP_DIR = Path(
    os.getenv("ANON_MAP_STORAGE_DIR", "./anon_maps")
).resolve()


def _map_path(document_id: int) -> Path:
    """Path to the encrypted map file for a document."""
    _ANON_MAP_DIR.mkdir(parents=True, exist_ok=True)
    return _ANON_MAP_DIR / f"{document_id}.enc"


def _serialize(anon_map: AnonymizationMap) -> bytes:
    """Serialize entity_map and language for persistence (deanonymization only)."""
    payload = {
        "entity_map": anon_map.entity_map,
        "language": anon_map.language,
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _deserialize(document_id: int, data: bytes) -> AnonymizationMap:
    """Deserialize encrypted payload into an AnonymizationMap."""
    payload = json.loads(data.decode("utf-8"))
    return AnonymizationMap(
        document_id=document_id,
        language=payload.get("language", "en"),
        entity_map=payload.get("entity_map", {}),
    )


def set_document_map(document_id: int, anon_map: AnonymizationMap) -> None:
    """Store the anonymization map in memory and persist it encrypted on disk."""
    _document_maps[document_id] = anon_map
    try:
        plaintext = _serialize(anon_map)
        associated_data = str(document_id).encode("utf-8")
        encrypted_bytes = encrypt(plaintext, associated_data=associated_data)
        _map_path(document_id).write_bytes(encrypted_bytes)
    except OSError:
        pass


def get_document_map(document_id: int) -> AnonymizationMap | None:
    """Return the anonymization map for a document.

    Checks in-memory cache first, then tries to load from encrypted file
    (so it works after restart or from another worker).
    """
    if document_id in _document_maps:
        return _document_maps[document_id]
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
        _document_maps[document_id] = anon_map
        return anon_map
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as e:
        logger.warning(
            "Could not load anonymization map for document_id=%s (deanonymization will be skipped): %s",
            document_id,
            type(e).__name__,
        )
        return None
    except Exception as e:
        logger.warning(
            "Could not load anonymization map for document_id=%s (deanonymization will be skipped): %s",
            document_id,
            type(e).__name__,
        )
        return None


def pop_document_map(document_id: int) -> None:
    """Remove the map from memory and delete the encrypted file."""
    _document_maps.pop(document_id, None)
    path = _map_path(document_id)
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass
