from __future__ import annotations

"""
FAISS-based vector store for Septum.

This module is responsible for:
- Computing multilingual sentence embeddings using a MiniLM-based model.
- Maintaining a separate FAISS index per document (document_id namespace).
- Persisting FAISS indexes to disk encrypted with AES-256-GCM.

Design notes
------------
- Embeddings are produced by a multilingual MiniLM SentenceTransformer model.
- Embeddings are L2-normalized and indexed with inner-product (cosine similarity).
- Each document's index is an `IndexIDMap` where FAISS vector IDs correspond
  directly to `Chunk.id` values in the database.
- Index files are serialized with FAISS and then encrypted with the shared
  AES-256-GCM utilities from ``app.utils.crypto`` before being written to disk.
"""

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import List, Sequence, Tuple

import faiss  # type: ignore[import]
import numpy as np
from sentence_transformers import SentenceTransformer

from ..utils.crypto import decrypt, encrypt
from ..utils.device import get_device
from cryptography.exceptions import InvalidTag


def _normalize_embeddings(embeddings: np.ndarray) -> np.ndarray:
    """L2-normalize embedding vectors along the last dimension; zero vectors unchanged."""
    if embeddings.ndim != 2:
        raise ValueError("embeddings must be a 2D array of shape (n_samples, dim).")
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return embeddings / norms


@dataclass
class VectorStore:
    """FAISS-backed vector store with per-document encrypted indexes.

    Parameters
    ----------
    base_dir:
        Directory where encrypted FAISS index files will be stored. If not
        provided, the value is taken from the ``VECTOR_INDEX_DIR`` environment
        variable or defaults to ``./vector_indexes``.
    model_name:
        HuggingFace model identifier for the multilingual MiniLM encoder.
    """

    base_dir: Path = field(
        default_factory=lambda: Path(
            os.getenv("VECTOR_INDEX_DIR", "./vector_indexes")
        )
    )
    model_name: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    _model: SentenceTransformer | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        """Ensure the base directory exists."""
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def index_document(
        self,
        document_id: int,
        chunk_ids: Sequence[int],
        texts: Sequence[str],
    ) -> None:
        """Build or replace the FAISS index for a document.

        The index will contain one vector per chunk, with FAISS vector IDs
        matching the provided ``chunk_ids``.

        Parameters
        ----------
        document_id:
            Identifier of the document whose chunks are being indexed.
        chunk_ids:
            Sequence of database chunk IDs (e.g., ``Chunk.id``) corresponding
            one-to-one with ``texts``.
        texts:
            Sequence of sanitized chunk texts to embed and index.
        """
        if len(chunk_ids) != len(texts):
            raise ValueError("chunk_ids and texts must have the same length.")

        if not chunk_ids:
            self.delete_index(document_id)
            return

        embeddings = self._encode_texts(texts)
        dim = embeddings.shape[1]
        base_index = faiss.IndexFlatIP(dim)
        index = faiss.IndexIDMap(base_index)

        ids_array = np.asarray(chunk_ids, dtype="int64")
        index.add_with_ids(embeddings, ids_array)

        self._save_index(document_id, index)

    def search(
        self,
        document_id: int,
        query: str,
        top_k: int,
    ) -> List[Tuple[int, float]]:
        """Search the document-specific index with a query string.

        Parameters
        ----------
        document_id:
            Identifier of the document to search within.
        query:
            Query text to embed and use for nearest-neighbour search.
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

        index = self._load_index(document_id)
        if index is None:
            return []

        query_vec = self._encode_texts([query])
        scores, ids = index.search(query_vec, top_k)

        results: List[Tuple[int, float]] = []
        for idx, score in zip(ids[0], scores[0]):
            if idx == -1:
                continue
            results.append((int(idx), float(score)))
        
        return results

    def delete_index(self, document_id: int) -> None:
        """Remove the stored FAISS index for a document, if it exists."""
        path = self._index_path(document_id)
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass

    def _get_model(self) -> SentenceTransformer:
        """Return the lazy-loaded multilingual MiniLM encoder."""
        if self._model is None:
            device = get_device()
            self._model = SentenceTransformer(self.model_name, device=device)
        return self._model

    def _encode_texts(self, texts: Sequence[str]) -> np.ndarray:
        """Encode texts into normalized embedding vectors."""
        if not texts:
            return np.zeros((0, 0), dtype="float32")
        model = self._get_model()
        embeddings = model.encode(
            list(texts),
            convert_to_numpy=True,
            show_progress_bar=False,
        ).astype("float32")
        return _normalize_embeddings(embeddings)

    def _index_path(self, document_id: int) -> Path:
        """Return the filesystem path for a document's encrypted FAISS index."""
        filename = f"doc_{document_id}.faiss.enc"
        return self.base_dir / filename

    def _save_index(self, document_id: int, index: faiss.Index) -> None:
        """Serialize, encrypt, and write a FAISS index to disk."""
        raw_bytes = faiss.serialize_index(index)
        raw_bytes_b = bytes(raw_bytes)
        associated_data = str(document_id).encode("utf-8")
        encrypted = encrypt(raw_bytes_b, associated_data=associated_data)

        path = self._index_path(document_id)
        path.write_bytes(encrypted)

    def _load_index(self, document_id: int) -> faiss.Index | None:
        """Load and decrypt a FAISS index for a document, if present."""
        path = self._index_path(document_id)
        if not path.exists():
            return None
        encrypted = path.read_bytes()
        associated_data = str(document_id).encode("utf-8")
        try:
            raw_bytes = decrypt(encrypted, associated_data=associated_data)
        except InvalidTag:
            try:
                path.unlink()
            except OSError:
                pass
            return None

        buffer = np.frombuffer(raw_bytes, dtype="uint8")
        return faiss.deserialize_index(buffer)


@dataclass
class VectorStoreService:
    """Async-friendly service wrapper around :class:`VectorStore`.

    This service exposes a higher-level interface that works directly with
    chunk objects (e.g. LangChain-style documents) which provide
    ``page_content`` and ``metadata['chunk_index']`` attributes. It is
    intended to be used by the document processing pipeline.
    """

    vector_store: VectorStore = field(default_factory=VectorStore)

    async def index(self, document_id: int, chunks: Sequence[object]) -> None:
        """Index the given chunks for a document.

        Parameters
        ----------
        document_id:
            Identifier of the document whose chunks are being indexed.
        chunks:
            Sequence of objects with ``page_content`` and ``metadata`` where
            ``metadata['chunk_index']`` provides a stable integer identifier
            for the chunk (typically the database ``Chunk.id`` or index).
        """
        if not chunks:
            self.vector_store.delete_index(document_id)
            return

        texts: List[str] = []
        chunk_ids: List[int] = []
        for chunk in chunks:
            text = getattr(chunk, "page_content", None)
            metadata = getattr(chunk, "metadata", {}) or {}
            chunk_index = metadata.get("chunk_index")
            if text is None or chunk_index is None:
                continue
            texts.append(str(text))
            chunk_ids.append(int(chunk_index))

        if not chunk_ids:
            self.vector_store.delete_index(document_id)
            return

        self.vector_store.index_document(document_id=document_id, chunk_ids=chunk_ids, texts=texts)

    async def retrieve(
        self,
        document_id: int,
        query: str,
        k: int,
    ) -> List[object]:
        """Retrieve top-k matching chunks for a query.

        This method returns lightweight objects containing only
        ``page_content`` and ``metadata['chunk_index']`` so that callers can
        map back to full chunk records if desired.
        """
        results = self.vector_store.search(document_id=document_id, query=query, top_k=k)
        retrieved: List[object] = []
        for chunk_id, score in results:
            doc = type(
                "RetrievedChunk",
                (),
                {
                    "page_content": "",
                    "metadata": {"chunk_index": chunk_id, "score": score},
                },
            )()
            retrieved.append(doc)
        return retrieved


