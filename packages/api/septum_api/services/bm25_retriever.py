"""
BM25-based sparse retriever for Septum.

This module provides keyword-based search using the BM25 algorithm,
complementing the dense FAISS vector search. BM25 is particularly effective
for exact phrase matching and keyword queries.

Design notes
------------
- Uses rank-bm25 (Okapi BM25 implementation)
- Each document has a separate BM25 index (document_id namespace)
- Indexes are persisted to disk with encryption (AES-256-GCM)
- Token-level matching (better for legal/contract terminology)
- Each index records the tokenizer version and language it was built with;
  an index of another version is never searched (see
  :meth:`BM25Retriever.is_current`), the caller rebuilds it from the
  stored chunks instead
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Sequence, Set, Tuple

from cryptography.exceptions import InvalidTag
from rank_bm25 import BM25Okapi
from septum_core.anonymization_map import PLACEHOLDER_SHAPE
from septum_core.text_boundaries import is_unspaced_char
from septum_core.text_utils import APOSTROPHES, normalize_for_comparison

from ..utils.crypto import decrypt, encrypt
from .index_file_io import document_ids_in, index_io_error, write_index_atomically

_log = logging.getLogger(__name__)

TOKENIZER_VERSION = 2
"""Version of :func:`_tokenize` stored in every index.

Bump it whenever tokenization changes: query and corpus must be tokenized
the same way, so an index built by another version is rebuilt, never
searched.
"""

_DEFAULT_LANGUAGE = "en"

_index_write_locks: "weakref.WeakValueDictionary[int, asyncio.Lock]" = (
    weakref.WeakValueDictionary()
)


def bm25_index_lock(document_id: int) -> asyncio.Lock:
    """Return this process's lock for writing ``document_id``'s BM25 index.

    Ingestion and the chat-time rebuild of an outdated index both write
    under it, so a rebuild from chunk rows read before a reprocess can
    never land after the reprocess wrote the fresh index. The caller must
    keep the returned lock referenced while it uses it.

    The lock is local to one process: it orders writers of a single worker
    only. With several workers a rebuild can still race a reprocess running
    in another one, so the rebuild also compares what it indexed (chunk ids,
    texts and language) with the document's current chunks after writing
    and removes its index on a mismatch (see the chat router's rebuild).
    That check runs under the document map lock, which is shared across
    workers only when Redis is configured: a multi-worker deployment needs
    Redis for it, as for every other map-lock guarantee.
    """
    lock = _index_write_locks.get(document_id)
    if lock is None:
        lock = _index_write_locks[document_id] = asyncio.Lock()
    return lock


# A placeholder ("[iban_1]" once lowercased) is one token; any other run of
# word characters is a token, so punctuation and every apostrophe variant
# split ("iban’a" -> "iban", "a"; "iban:" -> "iban"). The modifier-letter
# apostrophe counts as a word character, hence the explicit exclusion.
_APOSTROPHE_CLASS = re.escape("".join(sorted(APOSTROPHES)))
_TOKEN_RE = re.compile(rf"{PLACEHOLDER_SHAPE.pattern}|[^\W{_APOSTROPHE_CLASS}]+")


def _keeps(token: str) -> bool:
    """Drop lone letters of spaced scripts (a suffix cut off at an apostrophe)."""
    return len(token) > 1 or not token.isalpha() or is_unspaced_char(token)


def _tokenize(text: str, language: str = _DEFAULT_LANGUAGE) -> List[str]:
    """Split ``text`` into comparison-folded word and placeholder tokens.

    Casing follows the locale tables of :func:`normalize_for_comparison`
    for ``language`` (dotted and dotless I fold together where the locale
    pairs them), so the corpus and a query tokenized with the same language
    agree on every spelling.
    """
    folded = normalize_for_comparison(text, language)
    return [m.group(0) for m in _TOKEN_RE.finditer(folded) if _keeps(m.group(0))]


@dataclass
class BM25Retriever:
    """BM25-backed keyword retriever with per-document encrypted indexes.

    Parameters
    ----------
    base_dir:
        Directory where encrypted BM25 index files will be stored. If not
        provided, defaults to ``./bm25_indexes``.
    """

    base_dir: Path = field(
        default_factory=lambda: Path(
            os.getenv("BM25_INDEX_DIR", "./bm25_indexes")
        )
    )

    _current: Set[int] = field(default_factory=set, init=False, repr=False)

    def __post_init__(self) -> None:
        """Ensure the base directory exists."""
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def index_document(
        self,
        document_id: int,
        chunk_ids: Sequence[int],
        texts: Sequence[str],
        language: str = _DEFAULT_LANGUAGE,
    ) -> None:
        """Build or replace the BM25 index for a document.

        Parameters
        ----------
        document_id:
            Identifier of the document whose chunks are being indexed.
        chunk_ids:
            Sequence of database chunk IDs corresponding one-to-one with texts.
        texts:
            Sequence of chunk texts to tokenize and index.
        language:
            ISO 639-1 code whose casing rules tokenize the texts; stored with
            the index so every query is tokenized the same way.
        """
        if len(chunk_ids) != len(texts):
            raise ValueError("chunk_ids and texts must have the same length.")

        if not chunk_ids:
            self.delete_index(document_id)
            return

        index_data = {
            "corpus": [_tokenize(text, language) for text in texts],
            "chunk_ids": list(chunk_ids),
            "tokenizer_version": TOKENIZER_VERSION,
            "language": language,
        }

        self._save_index(document_id, index_data)
        self._current.add(document_id)

    def is_current(self, document_id: int) -> bool:
        """Return ``False`` when the index must be built: it is missing or of another tokenizer version.

        A missing index is reported so the caller rebuilds it from the stored
        chunks (one discarded after a failed rebuild, or never written); the
        caller decides whether the document has anything to index. An index
        that exists but cannot be read counts as current: it may be another
        worker's save caught mid-flight, which only a save or the document's
        erasure replaces. A verdict of ``True`` for a stored index is
        remembered by this instance, but the file is still checked to exist
        on every call, so an index another worker deleted is reported missing.
        """
        if not self._index_path(document_id).exists():
            self._current.discard(document_id)
            return False
        if document_id in self._current:
            return True
        index_data = self._load_index(document_id)
        if index_data is None:
            return True
        if not _built_by_this_tokenizer(index_data):
            return False
        self._current.add(document_id)
        return True

    def search(
        self,
        document_id: int,
        query: str,
        top_k: int,
    ) -> List[Tuple[int, float]]:
        """Search the document-specific BM25 index with a query string.

        Parameters
        ----------
        document_id:
            Identifier of the document to search within.
        query:
            Query text to tokenize and match against the corpus.
        top_k:
            Maximum number of results to return.

        Returns
        -------
        List[Tuple[int, float]]
            A list of ``(chunk_id, score)`` pairs ordered by descending score.
            If no index exists for the document, or it was built by another
            tokenizer version, an empty list is returned.
        """
        if top_k <= 0:
            return []

        index_data = self._load_index(document_id)
        if index_data is None:
            return []
        if not _built_by_this_tokenizer(index_data):
            _log.warning(
                "BM25 index for document %s was built by another tokenizer; not searched",
                document_id,
            )
            return []

        bm25 = BM25Okapi(index_data["corpus"])
        chunk_ids = index_data["chunk_ids"]

        tokenized_query = _tokenize(query, index_data.get("language") or _DEFAULT_LANGUAGE)
        scores = bm25.get_scores(tokenized_query)

        results = [
            (chunk_ids[i], float(scores[i]))
            for i in range(len(chunk_ids))
        ]
        results.sort(key=lambda x: x[1], reverse=True)

        return results[:top_k]

    def delete_index(self, document_id: int) -> None:
        """Remove the stored BM25 index for a document.

        A missing index counts as already erased; any other ``OSError``
        propagates so the caller cannot report the erasure as done.
        """
        self._current.discard(document_id)
        self._index_path(document_id).unlink(missing_ok=True)

    def stored_document_ids(self) -> set[int]:
        """Return the ids of every document that has a stored BM25 index."""
        return document_ids_in(self.base_dir, "doc_", ".bm25.enc")

    def _index_path(self, document_id: int) -> Path:
        """Return the filesystem path for a document's encrypted BM25 index."""
        filename = f"doc_{document_id}.bm25.enc"
        return self.base_dir / filename

    def _save_index(self, document_id: int, index_data: dict) -> None:
        """Serialize, encrypt, and write a BM25 index to disk."""
        raw_bytes = json.dumps(index_data).encode("utf-8")
        associated_data = str(document_id).encode("utf-8")
        encrypted = encrypt(raw_bytes, associated_data=associated_data)

        write_index_atomically(self._index_path(document_id), encrypted, "BM25", document_id)

    def _load_index(self, document_id: int) -> dict | None:
        """Load and decrypt a BM25 index for a document, if present.

        Filesystem failures other than a missing file are re-raised as a
        :class:`RuntimeError` naming the document, the tier and the error
        class, with the original exception (and its path) suppressed.
        """
        path = self._index_path(document_id)
        try:
            encrypted = path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise index_io_error("BM25", document_id, "read", exc) from None

        associated_data = str(document_id).encode("utf-8")

        try:
            raw_bytes = decrypt(encrypted, associated_data=associated_data)
        except InvalidTag as exc:
            _log.warning(
                "Unreadable BM25 index for document %s left in place (%s)",
                document_id,
                type(exc).__name__,
            )
            return None

        try:
            return json.loads(raw_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            _log.warning(
                "Corrupt BM25 index for document %s left in place (%s)",
                document_id,
                type(exc).__name__,
            )
            return None


def _built_by_this_tokenizer(index_data: dict) -> bool:
    """Whether a loaded index was tokenized by :data:`TOKENIZER_VERSION`."""
    return index_data.get("tokenizer_version") == TOKENIZER_VERSION
