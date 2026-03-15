from __future__ import annotations

"""
FAISS-based vector store for Septum with hybrid search support.

This module is responsible for:
- Computing multilingual sentence embeddings using a MiniLM-based model.
- Maintaining a separate FAISS index per document (document_id namespace).
- Persisting FAISS indexes to disk encrypted with AES-256-GCM.
- Hybrid search combining BM25 (keyword) and FAISS (semantic) with RRF scoring.

Design notes
------------
- Embeddings are produced by a multilingual MiniLM SentenceTransformer model.
- On Apple Silicon (MPS), the model is loaded on CPU to avoid a PyTorch meta-tensor
  bug when moving the model to MPS (NotImplementedError with .to(device)).
- Embeddings are L2-normalized and indexed with inner-product (cosine similarity).
- Each document's index is an `IndexIDMap` where FAISS vector IDs correspond
  directly to `Chunk.id` values in the database.
- Index files are serialized with FAISS and then encrypted with the shared
  AES-256-GCM utilities from ``app.utils.crypto`` before being written to disk.
- Hybrid search uses Reciprocal Rank Fusion (RRF) to combine BM25 and FAISS results.
"""

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import List, Sequence, Tuple

import faiss  # type: ignore[import]
import numpy as np
from sentence_transformers import SentenceTransformer

from ..utils.crypto import decrypt, encrypt
from cryptography.exceptions import InvalidTag
import json


def _normalize_embeddings(embeddings: np.ndarray) -> np.ndarray:
    """L2-normalize embedding vectors along the last dimension; zero vectors unchanged."""
    if embeddings.ndim != 2:
        raise ValueError("embeddings must be a 2D array of shape (n_samples, dim).")
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return embeddings / norms


def merge_rrf_result_lists(
    result_lists: List[List[Tuple[int, float]]],
    top_k: int,
    rrf_k: int = 60,
) -> List[Tuple[int, float]]:
    """Merge multiple (chunk_id, score) result lists using Reciprocal Rank Fusion.

    RRF score for a chunk_id: sum over each list of 1 / (rrf_k + rank).
    Used to combine e.g. user-query retrieval with document-theme retrieval
    without hardcoding any language or document-type logic.

    Parameters
    ----------
    result_lists:
        Each element is a list of (chunk_id, score) from one retrieval run.
        Scores in the input are ignored; only rank in each list is used.
    top_k:
        Maximum number of (chunk_id, rrf_score) pairs to return.
    rrf_k:
        RRF constant (default 60, same as in hybrid_search).

    Returns
    -------
    List[Tuple[int, float]]
        Merged list of (chunk_id, rrf_score) ordered by descending score.
    """
    if top_k <= 0 or not result_lists:
        return []
    rrf_scores: dict[int, float] = {}
    for result_list in result_lists:
        for rank, (chunk_id, _) in enumerate(result_list, start=1):
            rrf_scores[chunk_id] = rrf_scores.get(chunk_id, 0.0) + 1.0 / (rrf_k + rank)
    combined = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
    return combined[:top_k]


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
            # #region agent log
            try:
                with open(
                    "/Users/barisyerlikaya/Projects/Septum/.cursor/debug-314f97.log",
                    "a",
                    encoding="utf-8",
                ) as f:
                    f.write(
                        json.dumps(
                            {
                                "sessionId": "314f97",
                                "runId": "initial",
                                "hypothesisId": "H1",
                                "location": "services/vector_store.py:search",
                                "message": "faiss_index_missing",
                                "data": {
                                    "document_id": document_id,
                                    "top_k": top_k,
                                },
                                "timestamp": __import__("time").time(),
                            }
                        )
                        + "\n"
                    )
            except Exception:
                pass
            # #endregion
            return []

        query_vec = self._encode_texts([query])
        scores, ids = index.search(query_vec, top_k)

        results: List[Tuple[int, float]] = []
        for idx, score in zip(ids[0], scores[0]):
            if idx == -1:
                continue
            results.append((int(idx), float(score)))

        # #region agent log
        try:
            sample_results = [
                {"chunk_id": int(cid), "score": float(s)}
                for cid, s in results[:5]
            ]
            with open(
                "/Users/barisyerlikaya/Projects/Septum/.cursor/debug-314f97.log",
                "a",
                encoding="utf-8",
            ) as f:
                f.write(
                    json.dumps(
                        {
                            "sessionId": "314f97",
                            "runId": "initial",
                            "hypothesisId": "H2",
                            "location": "services/vector_store.py:search",
                            "message": "faiss_search_results",
                            "data": {
                                "document_id": document_id,
                                "top_k": top_k,
                                "result_count": len(results),
                                "sample_results": sample_results,
                            },
                            "timestamp": __import__("time").time(),
                        }
                    )
                    + "\n"
                )
        except Exception:
            pass
        # #endregion

        return results

    def hybrid_search(
        self,
        document_id: int,
        query: str,
        top_k: int,
        bm25_retriever: "BM25Retriever | None" = None,
        alpha: float = 0.5,
        beta: float = 0.5,
    ) -> List[Tuple[int, float]]:
        """Hybrid search combining BM25 (keyword) and FAISS (semantic) with RRF.

        Reciprocal Rank Fusion (RRF) formula:
            RRF_score(chunk) = Σ 1 / (k + rank_i)
        where k=60 (standard constant), rank_i is the rank in each retriever.

        Final score is weighted combination:
            final_score = alpha * faiss_rrf + beta * bm25_rrf

        Parameters
        ----------
        document_id:
            Identifier of the document to search within.
        query:
            Query text for both BM25 and FAISS search.
        top_k:
            Maximum number of results to return.
        bm25_retriever:
            Optional BM25Retriever instance. If None, falls back to FAISS-only.
        alpha:
            Weight for FAISS scores (default 0.5).
        beta:
            Weight for BM25 scores (default 0.5).

        Returns
        -------
        List[Tuple[int, float]]
            A list of ``(chunk_id, score)`` pairs ordered by descending score.
        """
        if top_k <= 0:
            return []

        # Get FAISS results
        faiss_results = self.search(document_id, query, top_k=min(top_k * 3, 50))

        # Get BM25 results if available
        bm25_results: List[Tuple[int, float]] = []
        if bm25_retriever is not None:
            bm25_results = bm25_retriever.search(document_id, query, top_k=min(top_k * 3, 50))

        # If only one retriever has results, return those
        if not faiss_results and not bm25_results:
            return []
        if not faiss_results:
            return bm25_results[:top_k]
        if not bm25_results:
            return faiss_results[:top_k]

        # Apply Reciprocal Rank Fusion (RRF)
        rrf_k = 60  # Standard RRF constant

        # Build RRF scores for FAISS
        faiss_rrf: dict[int, float] = {}
        for rank, (chunk_id, _) in enumerate(faiss_results, start=1):
            faiss_rrf[chunk_id] = 1.0 / (rrf_k + rank)

        # Build RRF scores for BM25
        bm25_rrf: dict[int, float] = {}
        for rank, (chunk_id, _) in enumerate(bm25_results, start=1):
            bm25_rrf[chunk_id] = 1.0 / (rrf_k + rank)

        # Combine scores
        all_chunk_ids = set(faiss_rrf.keys()) | set(bm25_rrf.keys())
        combined: List[Tuple[int, float]] = []

        for chunk_id in all_chunk_ids:
            faiss_score = faiss_rrf.get(chunk_id, 0.0)
            bm25_score = bm25_rrf.get(chunk_id, 0.0)
            final_score = alpha * faiss_score + beta * bm25_score
            combined.append((chunk_id, final_score))

        # Sort by descending score and return top_k
        combined.sort(key=lambda x: x[1], reverse=True)
        return combined[:top_k]

    def delete_index(self, document_id: int) -> None:
        """Remove the stored FAISS index for a document, if it exists."""
        path = self._index_path(document_id)
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass

    def _get_model(self) -> SentenceTransformer:
        """Return the lazy-loaded multilingual MiniLM encoder.

        Loads on CPU with low_cpu_mem_usage=False to avoid PyTorch meta-tensor
        errors (PyTorch 2.2+). If that still raises NotImplementedError (e.g. on
        Apple MPS), retries without device so the library uses its default path.
        """
        if self._model is None:
            try:
                self._model = SentenceTransformer(
                    self.model_name,
                    device="cpu",
                    model_kwargs={"low_cpu_mem_usage": False},
                )
            except NotImplementedError:
                self._model = SentenceTransformer(self.model_name)
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


