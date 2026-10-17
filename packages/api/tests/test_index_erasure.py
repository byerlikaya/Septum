"""Index deletion tolerates a missing file and surfaces any other failure."""

from __future__ import annotations

from pathlib import Path

import pytest
from septum_api.services.bm25_retriever import BM25Retriever
from septum_api.services.vector_store import VectorStore


@pytest.fixture(params=["faiss", "bm25"])
def store(request: pytest.FixtureRequest, tmp_path: Path):
    if request.param == "faiss":
        return VectorStore(base_dir=tmp_path)
    return BM25Retriever(base_dir=tmp_path)


def test_missing_index_is_already_erased(store) -> None:
    store.delete_index(1)


def test_existing_index_is_removed(store) -> None:
    path = store._index_path(1)
    path.write_bytes(b"ciphertext")

    store.delete_index(1)

    assert not path.exists()


def test_other_os_error_propagates(store) -> None:
    store._index_path(1).mkdir()

    with pytest.raises(OSError) as excinfo:
        store.delete_index(1)

    assert not isinstance(excinfo.value, FileNotFoundError)
