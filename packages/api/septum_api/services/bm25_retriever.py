from __future__ import annotations

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
"""

import os
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Sequence, Tuple

from cryptography.exceptions import InvalidTag
from rank_bm25 import BM25Okapi

from ..utils.crypto import decrypt, encrypt


def _tokenize(text: str) -> List[str]:
    """Simple whitespace tokenizer with lowercase normalization.

    For production, consider more sophisticated tokenization:
    - Language-specific stemmers
    - N-gram tokens for better recall
    - Entity-aware tokenization (preserve [PLACEHOLDER_N] tokens intact)
    """
    return text.lower().split()


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

    def __post_init__(self) -> None:
        """Ensure the base directory exists."""
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def index_document(
        self,
        document_id: int,
        chunk_ids: Sequence[int],
        texts: Sequence[str],
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
        """
        if len(chunk_ids) != len(texts):
            raise ValueError("chunk_ids and texts must have the same length.")

        if not chunk_ids:
            self.delete_index(document_id)
            return

        tokenized_corpus = [_tokenize(text) for text in texts]

        index_data = {
            "corpus": tokenized_corpus,
            "chunk_ids": list(chunk_ids),
        }

        self._save_index(document_id, index_data)

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
            If no index exists for the document, an empty list is returned.
        """
        if top_k <= 0:
            return []

        index_data = self._load_index(document_id)
        if index_data is None:
            return []

        bm25 = BM25Okapi(index_data["corpus"])
        chunk_ids = index_data["chunk_ids"]

        tokenized_query = _tokenize(query)
        scores = bm25.get_scores(tokenized_query)

        results = [
            (chunk_ids[i], float(scores[i]))
            for i in range(len(chunk_ids))
        ]
        results.sort(key=lambda x: x[1], reverse=True)

        return results[:top_k]

    def delete_index(self, document_id: int) -> None:
        """Remove the stored BM25 index for a document, if it exists."""
        path = self._index_path(document_id)
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass

    def _index_path(self, document_id: int) -> Path:
        """Return the filesystem path for a document's encrypted BM25 index."""
        filename = f"doc_{document_id}.bm25.enc"
        return self.base_dir / filename

    def _save_index(self, document_id: int, index_data: dict) -> None:
        """Serialize, encrypt, and write a BM25 index to disk."""
        raw_bytes = json.dumps(index_data).encode("utf-8")
        associated_data = str(document_id).encode("utf-8")
        encrypted = encrypt(raw_bytes, associated_data=associated_data)

        path = self._index_path(document_id)
        path.write_bytes(encrypted)

    def _load_index(self, document_id: int) -> dict | None:
        """Load and decrypt a BM25 index for a document, if present."""
        path = self._index_path(document_id)
        if not path.exists():
            return None

        encrypted = path.read_bytes()
        associated_data = str(document_id).encode("utf-8")

        try:
            raw_bytes = decrypt(encrypted, associated_data=associated_data)
        except InvalidTag:
            # Invalid encryption key or corrupted file; delete and return None
            try:
                path.unlink()
            except OSError:
                pass
            return None

        return json.loads(raw_bytes)
