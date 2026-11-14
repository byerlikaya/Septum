"""Index files are written atomically and never deleted by a read.

Covers the atomic save and load paths of the FAISS and BM25 stores, the
startup sweep of temp files a crashed save left behind, and the
``cleanup_artifacts`` failure contract used by the reprocess flow. A
reader that meets an unreadable file reports it and leaves it alone: it
may be another worker's save caught mid-flight on a filesystem without
atomic rename, and only a save or the document's erasure replaces it.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import faiss
import numpy as np
import pytest
from septum_api.services import document_pipeline as pipeline_module
from septum_api.services.bm25_retriever import BM25Retriever
from septum_api.services.document_pipeline import DocumentPipeline
from septum_api.services.index_file_io import sweep_stale_index_temp_files
from septum_api.services.vector_store import VectorStore
from septum_api.utils.crypto import encrypt

TAMPERED = b"x" * 48


def _bm25_store(tmp_path: Path) -> BM25Retriever:
    return BM25Retriever(base_dir=tmp_path)


def _faiss_store(tmp_path: Path) -> VectorStore:
    return VectorStore(base_dir=tmp_path)


@pytest.mark.parametrize(
    ("make_store", "document_id", "label"), [(_bm25_store, 7, "BM25"), (_faiss_store, 11, "FAISS")]
)
def test_tampered_index_is_kept_and_logged(
    make_store, document_id: int, label: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = make_store(tmp_path)
    path = store._index_path(document_id)
    path.write_bytes(b"not-a-valid-ciphertext-at-all-0000000000")

    with caplog.at_level(logging.WARNING):
        assert store._load_index(document_id) is None

    assert path.read_bytes() == b"not-a-valid-ciphertext-at-all-0000000000"
    assert f"{label} index for document {document_id}" in caplog.text
    assert "InvalidTag" in caplog.text
    assert str(tmp_path) not in caplog.text


def test_bm25_non_json_plaintext_is_kept_and_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = _bm25_store(tmp_path)
    path = store._index_path(8)
    path.write_bytes(encrypt(b"\xff\xfe not json", associated_data=b"8"))

    with caplog.at_level(logging.WARNING):
        assert store._load_index(8) is None

    assert path.exists()
    assert "document 8" in caplog.text
    assert "UnicodeDecodeError" in caplog.text or "JSONDecodeError" in caplog.text


def _faiss_index() -> faiss.Index:
    index = faiss.IndexFlatIP(4)
    index.add(np.ones((2, 4), dtype="float32"))
    return index


@pytest.mark.parametrize(
    ("make_store", "payload"), [(_bm25_store, {"k": [1, 2]}), (_faiss_store, None)]
)
def test_save_round_trips_and_leaves_no_temp_file(make_store, payload, tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store._save_index(5, payload if payload is not None else _faiss_index())

    loaded = store._load_index(5)
    if payload is not None:
        assert loaded == payload
    else:
        assert loaded.ntotal == 2
    assert [p.name for p in tmp_path.iterdir()] == [store._index_path(5).name]
    assert oct(store._index_path(5).stat().st_mode & 0o777) == "0o600"


@pytest.mark.parametrize(
    ("make_store", "payload"), [(_bm25_store, {"k": [1]}), (_faiss_store, None)]
)
def test_failed_save_keeps_the_previous_index_and_no_temp_file(
    make_store, payload, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = make_store(tmp_path)
    path = store._index_path(6)
    path.write_bytes(b"previous")

    def _fail(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", _fail)
    with pytest.raises(RuntimeError) as excinfo:
        store._save_index(6, payload if payload is not None else _faiss_index())

    assert path.read_bytes() == b"previous"
    assert [p.name for p in tmp_path.iterdir()] == [path.name]
    _assert_path_free(excinfo.value, tmp_path, 6, "OSError")


@pytest.mark.parametrize("make_store", [_bm25_store, _faiss_store])
def test_temp_files_are_not_stored_indexes(make_store, tmp_path: Path) -> None:
    store = make_store(tmp_path)
    temp = tmp_path / f"{store._index_path(4).name}.0123abcd.tmp"
    temp.write_bytes(b"partial")
    assert store.stored_document_ids() == set()


def test_sweep_removes_only_stale_temp_files(tmp_path: Path) -> None:
    stale = tmp_path / "doc_1.bm25.enc.aa.tmp"
    fresh = tmp_path / "doc_2.bm25.enc.bb.tmp"
    index = tmp_path / "doc_3.bm25.enc"
    for path in (stale, fresh, index):
        path.write_bytes(b"x")
    old = time.time() - 3600
    os.utime(stale, (old, old))
    os.utime(index, (old, old))

    assert sweep_stale_index_temp_files(tmp_path) == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == [fresh.name, index.name]
    assert sweep_stale_index_temp_files(tmp_path / "missing") == 0


def test_bm25_missing_index_returns_none(tmp_path: Path) -> None:
    assert _bm25_store(tmp_path)._load_index(9) is None


def _assert_path_free(
    error: BaseException, root: Path, document_id: int, error_class: str
) -> None:
    assert f"document {document_id}" in str(error)
    assert error_class in str(error)
    assert str(root) not in str(error)
    assert error.__cause__ is None
    assert error.__suppress_context__ is True


def _deny_read(self: Path) -> bytes:
    raise PermissionError(13, "Permission denied", str(self))


@pytest.mark.parametrize("make_store", [_bm25_store, _faiss_store])
def test_read_failure_raises_without_path(
    make_store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = make_store(tmp_path)
    store._index_path(13).write_bytes(TAMPERED)
    monkeypatch.setattr(Path, "read_bytes", _deny_read)

    with pytest.raises(RuntimeError) as excinfo:
        store._load_index(13)

    _assert_path_free(excinfo.value, tmp_path, 13, "PermissionError")


class _FakeVectorStore:
    failure: BaseException | None = None
    deleted: list[int] = []

    def delete_index(self, document_id: int) -> None:
        if _FakeVectorStore.failure is not None:
            raise _FakeVectorStore.failure
        _FakeVectorStore.deleted.append(document_id)


class _FakeBM25Retriever:
    failure: BaseException | None = None
    deleted: list[int] = []

    def delete_index(self, document_id: int) -> None:
        if _FakeBM25Retriever.failure is not None:
            raise _FakeBM25Retriever.failure
        _FakeBM25Retriever.deleted.append(document_id)


@pytest.fixture
def cleanup_env(monkeypatch: pytest.MonkeyPatch):
    popped: list[int] = []

    async def _pop(document_id: int) -> None:
        popped.append(document_id)

    _FakeVectorStore.failure = None
    _FakeVectorStore.deleted = []
    _FakeBM25Retriever.failure = None
    _FakeBM25Retriever.deleted = []
    monkeypatch.setattr(pipeline_module, "VectorStore", _FakeVectorStore)
    monkeypatch.setattr(pipeline_module, "BM25Retriever", _FakeBM25Retriever)
    monkeypatch.setattr(pipeline_module, "pop_document_map", _pop)
    return popped


class _HeldClaimSession:
    """A session whose claim check finds the row held by the run."""

    async def scalar(self, statement) -> int:
        return 1

    async def commit(self) -> None:
        return None


async def _cleanup(document_id: int) -> None:
    await DocumentPipeline.cleanup_artifacts(document_id, db=_HeldClaimSession(), run_token="t")


async def test_cleanup_artifacts_removes_all_tiers(cleanup_env: list[int]) -> None:
    await _cleanup(3)

    assert _FakeVectorStore.deleted == [3]
    assert _FakeBM25Retriever.deleted == [3]
    assert cleanup_env == [3]


async def test_cleanup_artifacts_faiss_failure_propagates_without_path(
    cleanup_env: list[int],
) -> None:
    _FakeVectorStore.failure = PermissionError(
        13, "Permission denied", "/secret/vector_indexes/doc_4.faiss.enc"
    )

    with pytest.raises(RuntimeError) as excinfo:
        await _cleanup(4)

    message = str(excinfo.value)
    assert "document 4" in message
    assert "PermissionError" in message
    assert "/secret" not in message
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__ is True
    assert _FakeBM25Retriever.deleted == []
    assert cleanup_env == []


async def test_cleanup_artifacts_bm25_failure_propagates_without_path(
    cleanup_env: list[int],
) -> None:
    _FakeBM25Retriever.failure = OSError(5, "I/O error", "/secret/bm25_indexes/doc_5.bm25.enc")

    with pytest.raises(RuntimeError) as excinfo:
        await _cleanup(5)

    message = str(excinfo.value)
    assert "BM25" in message
    assert "document 5" in message
    assert "/secret" not in message
    assert cleanup_env == []


async def test_cleanup_artifacts_map_erasure_failure_propagates_without_path(
    cleanup_env: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    from septum_api.services.document_anon_store import DocumentMapErasureError

    async def _pop(document_id: int) -> None:
        try:
            raise PermissionError(13, "Permission denied", "/secret/anon_maps/6.enc")
        except PermissionError as exc:
            raise DocumentMapErasureError("not erased from: disk") from exc

    monkeypatch.setattr(pipeline_module, "pop_document_map", _pop)

    with pytest.raises(RuntimeError) as excinfo:
        await _cleanup(6)

    message = str(excinfo.value)
    assert "anonymization map" in message
    assert "document 6" in message
    assert "DocumentMapErasureError" in message
    assert "/secret" not in message
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__ is True
    assert _FakeVectorStore.deleted == [6]
    assert _FakeBM25Retriever.deleted == [6]


async def test_cleanup_artifacts_non_os_error_is_also_path_free(
    cleanup_env: list[int],
) -> None:
    _FakeVectorStore.failure = ValueError("/secret/vector_indexes/doc_7.faiss.enc is corrupt")

    with pytest.raises(RuntimeError) as excinfo:
        await _cleanup(7)

    message = str(excinfo.value)
    assert "document 7" in message
    assert "ValueError" in message
    assert "/secret" not in message
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__ is True
    assert cleanup_env == []
