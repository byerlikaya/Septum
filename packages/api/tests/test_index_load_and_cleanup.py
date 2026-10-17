"""Corrupt index files are discarded; other filesystem failures surface.

Covers the load-path discard in the FAISS and BM25 stores and the
``cleanup_artifacts`` failure contract used by the reprocess flow.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from septum_api.services import document_pipeline as pipeline_module
from septum_api.services.bm25_retriever import BM25Retriever
from septum_api.services.document_pipeline import DocumentPipeline
from septum_api.services.vector_store import VectorStore
from septum_api.utils.crypto import encrypt

TAMPERED = b"x" * 48


def _bm25_store(tmp_path: Path) -> BM25Retriever:
    return BM25Retriever(base_dir=tmp_path)


def _faiss_store(tmp_path: Path) -> VectorStore:
    return VectorStore(base_dir=tmp_path)


def test_bm25_tampered_index_is_discarded_and_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = _bm25_store(tmp_path)
    path = store._index_path(7)
    path.write_bytes(b"not-a-valid-ciphertext-at-all-0000000000")

    with caplog.at_level(logging.WARNING):
        assert store._load_index(7) is None

    assert not path.exists()
    assert "document 7" in caplog.text
    assert "InvalidTag" in caplog.text
    assert str(tmp_path) not in caplog.text


def test_bm25_non_json_plaintext_is_discarded_and_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = _bm25_store(tmp_path)
    path = store._index_path(8)
    path.write_bytes(encrypt(b"\xff\xfe not json", associated_data=b"8"))

    with caplog.at_level(logging.WARNING):
        assert store._load_index(8) is None

    assert not path.exists()
    assert "document 8" in caplog.text
    assert "UnicodeDecodeError" in caplog.text or "JSONDecodeError" in caplog.text


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


def test_bm25_unlink_failure_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _bm25_store(tmp_path)
    store._index_path(10).write_bytes(TAMPERED)

    def _deny(self: Path, missing_ok: bool = False) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "unlink", _deny)

    with pytest.raises(RuntimeError) as excinfo:
        store._load_index(10)

    _assert_path_free(excinfo.value, tmp_path, 10, "PermissionError")


def test_faiss_tampered_index_is_discarded_and_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = _faiss_store(tmp_path)
    path = store._index_path(11)
    path.write_bytes(b"not-a-valid-ciphertext-at-all-0000000000")

    with caplog.at_level(logging.WARNING):
        assert store._load_index(11) is None

    assert not path.exists()
    assert "document 11" in caplog.text
    assert "InvalidTag" in caplog.text
    assert str(tmp_path) not in caplog.text


def test_faiss_unlink_failure_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _faiss_store(tmp_path)
    store._index_path(12).write_bytes(TAMPERED)

    def _deny(self: Path, missing_ok: bool = False) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "unlink", _deny)

    with pytest.raises(RuntimeError) as excinfo:
        store._load_index(12)

    _assert_path_free(excinfo.value, tmp_path, 12, "PermissionError")


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


async def test_cleanup_artifacts_removes_all_tiers(cleanup_env: list[int]) -> None:
    await DocumentPipeline.cleanup_artifacts(3)

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
        await DocumentPipeline.cleanup_artifacts(4)

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
        await DocumentPipeline.cleanup_artifacts(5)

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
        await DocumentPipeline.cleanup_artifacts(6)

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
        await DocumentPipeline.cleanup_artifacts(7)

    message = str(excinfo.value)
    assert "document 7" in message
    assert "ValueError" in message
    assert "/secret" not in message
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__ is True
    assert cleanup_env == []
