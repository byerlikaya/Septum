"""Process-local cache of chunk text re-masked under the current policy.

Chat re-masks the retrieved chunks of an outdated document on every turn
until the document is reprocessed. The result for one chunk depends only on
the stored chunk text, the policy and the document map, and every
placeholder it introduces is persisted in that map, so it can be reused:
the key is ``(document id, chunk id, policy cache key, hash of the stored
text, masking language)``, and a hit is used only while the current
document map still knows every placeholder in it. Entries hold masked
text only, never an original.

The policy part is :attr:`PolicySnapshot.cache_key` (digest and entity
types), not the staleness digest alone: that one leaves the types out, and
a hit after a type was activated would send its values unmasked.

A reprocess writes new chunk rows (new ids, new text) and a new digest, and
the pipeline drops the document's entries when it starts or erases a
document; any policy change alters the cache key. The cache is bounded by
entry count and by total characters (least recently used entries go first)
and lives in one worker process.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from typing import Optional, Tuple

from .anonymization_map import AnonymizationMap
from .pii_gate import all_placeholders_known

REMASK_CACHE_MAX_ENTRIES = 4096
REMASK_CACHE_MAX_CHARS = 8_000_000

_Key = Tuple[int, int, str, str, str]


class RemaskCache:
    """LRU of re-masked chunk text, bounded by entry count and total characters."""

    def __init__(
        self,
        max_entries: int = REMASK_CACHE_MAX_ENTRIES,
        max_chars: int = REMASK_CACHE_MAX_CHARS,
    ) -> None:
        self._max_entries = max_entries
        self._max_chars = max_chars
        self._chars = 0
        self._entries: "OrderedDict[_Key, str]" = OrderedDict()

    @staticmethod
    def key(
        document_id: int, chunk_id: int, policy_key: str, stored_text: str, language: str
    ) -> _Key:
        """Return the cache key of one stored chunk under one policy and masking language.

        The language is part of the key so text re-masked in a language the
        document has since been corrected away from is never served.
        """
        text_hash = hashlib.sha256(stored_text.encode("utf-8")).hexdigest()
        return (document_id, chunk_id, policy_key, text_hash, language)

    def get(self, key: _Key, anon_map: AnonymizationMap) -> Optional[str]:
        """Return the cached text if its placeholders all resolve in ``anon_map``."""
        text = self._entries.get(key)
        if text is None:
            return None
        if not all_placeholders_known(text, anon_map):
            self._drop(key)
            return None
        self._entries.move_to_end(key)
        return text

    def put(self, key: _Key, text: str) -> None:
        """Store ``text``, evicting least recently used entries until both caps hold.

        A text longer than the whole character budget is not stored.
        """
        if key in self._entries:
            self._drop(key)
        if len(text) > self._max_chars:
            return
        self._entries[key] = text
        self._chars += len(text)
        while len(self._entries) > self._max_entries or self._chars > self._max_chars:
            _, evicted = self._entries.popitem(last=False)
            self._chars -= len(evicted)

    def forget_document(self, document_id: int) -> None:
        """Drop every entry of ``document_id``."""
        for key in [k for k in self._entries if k[0] == document_id]:
            self._drop(key)

    def clear(self) -> None:
        """Drop every entry."""
        self._entries.clear()
        self._chars = 0

    @property
    def stored_chars(self) -> int:
        """Total characters held."""
        return self._chars

    def _drop(self, key: _Key) -> None:
        self._chars -= len(self._entries.pop(key))

    def __len__(self) -> int:
        return len(self._entries)


remask_cache = RemaskCache()

__all__ = ["REMASK_CACHE_MAX_CHARS", "REMASK_CACHE_MAX_ENTRIES", "RemaskCache", "remask_cache"]
