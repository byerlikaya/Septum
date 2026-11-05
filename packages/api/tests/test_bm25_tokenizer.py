"""BM25 tokenization splits on punctuation and apostrophes; old indexes are rebuilt, never mixed.

Regression: the whitespace tokenizer turned "IBAN’a" and "IBAN:" into tokens
that matched nothing, so a question naming a field label got no keyword
signal at all.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, AsyncIterator

import pytest
from septum_api.models import Base
from septum_api.models.document import Chunk, Document
from septum_api.models.user import User
from septum_api.routers import chat as chat_module
from septum_api.services import document_anon_store
from septum_api.services.bm25_retriever import (
    TOKENIZER_VERSION,
    BM25Retriever,
    _tokenize,
    bm25_index_lock,
)
from septum_api.utils.crypto import encrypt
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

PAYMENT = "Ücret, işçinin bildirdiği TR94 0006 IBAN numaralı banka hesabına yatırılacaktır."
SIGNERS = "İŞVEREN ve İŞÇİ sözleşmeyi imzalamıştır."
LABEL = "IBAN:\nTR94 0006"


@pytest.mark.parametrize(
    ("text", "tokens"),
    [
        ("IBAN’a", ["iban"]),
        ("IBAN'a", ["iban"]),
        ("IBANʼa", ["iban"]),
        ("IBAN:", ["iban"]),
        ("Ödeme: IBAN, (no)", ["ödeme", "iban", "no"]),
        ("[PERSON_NAME_2] maaşı", ["[person_name_2]", "maaşı"]),
        ("[Project code_1] x2", ["[project code_1]", "x2"]),
        ("Madde 2", ["madde", "2"]),
        ("请联系王芳", ["请联系王芳"]),
    ],
)
def test_tokens_split_on_punctuation_and_keep_placeholders(text: str, tokens: list[str]) -> None:
    assert _tokenize(text) == tokens


def test_locale_casing_folds_both_capital_i_forms() -> None:
    assert _tokenize("IBAN İŞVEREN", "tr") == _tokenize("iban işveren", "tr") == ["iban", "işveren"]


def test_query_with_suffixed_label_finds_the_clause(tmp_path: Path) -> None:
    store = BM25Retriever(base_dir=tmp_path)
    store.index_document(1, [10, 11, 12], [SIGNERS, PAYMENT, "Çalışma süresi 45 saattir."], "tr")

    results = store.search(1, "[PERSON_NAME_2] maaşı hangi IBAN’a yatırılıyor?", top_k=3)

    assert results[0][0] == 11
    assert results[0][1] > 0


def test_query_is_tokenized_in_the_index_language(tmp_path: Path) -> None:
    store = BM25Retriever(base_dir=tmp_path)
    store.index_document(1, [10, 11, 12], [SIGNERS, "Çalışma süresi", "Fesih"], "tr")

    [(top, score), *_] = store.search(1, "işveren kim", top_k=3)

    assert (top, score > 0) == (10, True)


def _write_legacy_index(store: BM25Retriever, document_id: int, chunk_ids: list[int]) -> None:
    payload = {"corpus": [["iban:"] for _ in chunk_ids], "chunk_ids": chunk_ids}
    store._index_path(document_id).write_bytes(
        encrypt(json.dumps(payload).encode(), associated_data=str(document_id).encode())
    )


def test_index_of_another_tokenizer_is_never_searched_or_deleted(tmp_path: Path) -> None:
    store = BM25Retriever(base_dir=tmp_path)
    _write_legacy_index(store, 3, [1])
    before = store._index_path(3).read_bytes()

    assert store.is_current(3) is False
    assert store.search(3, "iban", top_k=5) == []
    assert store._index_path(3).read_bytes() == before


def test_new_index_records_the_tokenizer(tmp_path: Path) -> None:
    store = BM25Retriever(base_dir=tmp_path)
    store.index_document(4, [1], [LABEL], "tr")

    data = store._load_index(4)
    assert (data["tokenizer_version"], data["language"]) == (TOKENIZER_VERSION, "tr")
    assert BM25Retriever(base_dir=tmp_path).is_current(4) is True


def test_missing_index_needs_a_build(tmp_path: Path) -> None:
    assert BM25Retriever(base_dir=tmp_path).is_current(9) is False


def test_unreadable_index_is_left_alone(tmp_path: Path) -> None:
    store = BM25Retriever(base_dir=tmp_path)
    store._index_path(9).write_bytes(b"not-a-valid-ciphertext-at-all-0000000000")

    assert store.is_current(9) is True


@pytest.fixture
async def session_maker(tmp_path: Path) -> AsyncIterator[Any]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'bm25.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(bind=engine, expire_on_commit=False)
    await engine.dispose()


async def _add_document(session_maker: Any, status: str = "completed") -> tuple[int, list[int]]:
    async with session_maker() as session:
        user = User(email="owner@example.com", hashed_password="x", role="editor")
        session.add(user)
        await session.flush()
        doc = Document(
            filename="stored.pdf",
            original_filename="contract.pdf",
            file_type="application/pdf",
            file_format="pdf",
            detected_language="tr",
            encrypted_path="/nonexistent",
            file_size_bytes=10,
            ocr_confidence=0.0,
            active_regulation_ids=[],
            sanitized_entity_types=[],
            sanitized_language="tr",
            ingestion_status=status,
            chunk_count=2,
            user_id=user.id,
        )
        session.add(doc)
        await session.flush()
        rows = [
            Chunk(document_id=doc.id, index=0, sanitized_text="[ORG_1] ve [PERSON_1]",
                  raw_text=SIGNERS, char_count=1),
            Chunk(document_id=doc.id, index=1, sanitized_text="masked", raw_text=None,
                  char_count=1),
        ]
        session.add_all(rows)
        await session.commit()
        return doc.id, [r.id for r in rows]


async def test_outdated_index_is_rebuilt_from_stored_chunks(
    session_maker: Any, tmp_path: Path
) -> None:
    doc_id, chunk_ids = await _add_document(session_maker)
    store = BM25Retriever(base_dir=tmp_path / "bm25")
    _write_legacy_index(store, doc_id, chunk_ids)

    async with session_maker() as db:
        await chat_module._rebuild_outdated_bm25_index(db, store, doc_id)

    data = BM25Retriever(base_dir=tmp_path / "bm25")._load_index(doc_id)
    assert data["tokenizer_version"] == TOKENIZER_VERSION
    assert data["language"] == "tr"
    assert data["chunk_ids"] == chunk_ids
    assert data["corpus"] == [_tokenize(SIGNERS, "tr"), ["masked"]]
    assert not list((tmp_path / "bm25").glob("*.tmp"))


async def test_document_still_ingesting_is_not_rebuilt(
    session_maker: Any, tmp_path: Path
) -> None:
    doc_id, chunk_ids = await _add_document(session_maker, status="processing")
    store = BM25Retriever(base_dir=tmp_path / "bm25")
    _write_legacy_index(store, doc_id, chunk_ids)
    before = store._index_path(doc_id).read_bytes()

    async with session_maker() as db:
        await chat_module._rebuild_outdated_bm25_index(db, store, doc_id)

    assert store._index_path(doc_id).read_bytes() == before


async def test_rebuild_waits_for_a_fresh_write_and_keeps_it(
    session_maker: Any, tmp_path: Path
) -> None:
    doc_id, chunk_ids = await _add_document(session_maker)
    store = BM25Retriever(base_dir=tmp_path / "bm25")
    _write_legacy_index(store, doc_id, chunk_ids)

    async with session_maker() as db:
        async with bm25_index_lock(doc_id):
            rebuild = asyncio.create_task(
                chat_module._rebuild_outdated_bm25_index(db, store, doc_id)
            )
            await asyncio.sleep(0.05)
            assert not rebuild.done()
            BM25Retriever(base_dir=tmp_path / "bm25").index_document(
                doc_id, [999], ["fresh reprocess text"], "tr"
            )
        await rebuild

    assert store._load_index(doc_id)["chunk_ids"] == [999]


async def test_rebuild_of_a_document_deleted_meanwhile_leaves_no_index(
    session_maker: Any, tmp_path: Path
) -> None:
    doc_id, chunk_ids = await _add_document(session_maker)
    store = BM25Retriever(base_dir=tmp_path / "bm25")
    _write_legacy_index(store, doc_id, chunk_ids)
    write = store.index_document

    def write_then_delete_row(*args: Any) -> None:
        write(*args)
        with sqlite3.connect(tmp_path / "bm25.db") as conn:
            conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))

    store.index_document = write_then_delete_row

    async with session_maker() as db:
        await chat_module._rebuild_outdated_bm25_index(db, store, doc_id)

    assert not store._index_path(doc_id).exists()


def test_capital_dotted_i_query_meets_the_index_in_its_language(tmp_path: Path) -> None:
    store = BM25Retriever(base_dir=tmp_path)
    store.index_document(1, [10, 11, 12], ["işveren imzaladı", "Çalışma süresi", "Fesih"], "tr")

    [(top, score), *_] = store.search(1, "İŞVEREN", top_k=3)

    assert (top, score > 0) == (10, True)


async def test_chat_retrieval_rebuilds_an_outdated_index(
    session_maker: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc_id, chunk_ids = await _add_document(session_maker)
    store = BM25Retriever(base_dir=tmp_path / "bm25")
    _write_legacy_index(store, doc_id, chunk_ids)

    class _NoVectors:
        def hybrid_search(self, **kwargs: Any) -> list:
            return []

    monkeypatch.setattr(chat_module, "_get_bm25_retriever", lambda: store)
    monkeypatch.setattr(chat_module, "_get_vector_store", lambda: _NoVectors())

    async with session_maker() as db:
        await chat_module._retrieve_chunks(db, doc_id, "işveren", top_k=5, chunk_count=2)

    data = BM25Retriever(base_dir=tmp_path / "bm25")._load_index(doc_id)
    assert data["tokenizer_version"] == TOKENIZER_VERSION


@pytest.mark.parametrize("failure", [RuntimeError("lock timeout"), asyncio.CancelledError()])
async def test_failed_post_check_removes_the_rebuilt_index(
    session_maker: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> None:
    doc_id, chunk_ids = await _add_document(session_maker)
    store = BM25Retriever(base_dir=tmp_path / "bm25")
    _write_legacy_index(store, doc_id, chunk_ids)

    async def fail(db: Any, document_id: int, indexed_ids: set) -> bool:
        raise failure

    monkeypatch.setattr(chat_module, "_rebuilt_index_still_valid", fail)

    with pytest.raises(type(failure)):
        async with session_maker() as db:
            await chat_module._rebuild_outdated_bm25_index(db, store, doc_id)

    assert not store._index_path(doc_id).exists()


async def test_rebuild_removes_its_index_when_the_chunks_changed_meanwhile(
    session_maker: Any, tmp_path: Path
) -> None:
    doc_id, chunk_ids = await _add_document(session_maker)
    store = BM25Retriever(base_dir=tmp_path / "bm25")
    _write_legacy_index(store, doc_id, chunk_ids)
    write = store.index_document

    def write_then_reprocess_elsewhere(*args: Any) -> None:
        write(*args)
        with sqlite3.connect(tmp_path / "bm25.db") as conn:
            conn.execute(
                "INSERT INTO chunks (document_id, \"index\", sanitized_text, char_count, chunk_type)"
                " VALUES (?, 2, 'new', 3, 'text')",
                (doc_id,),
            )

    store.index_document = write_then_reprocess_elsewhere

    async with session_maker() as db:
        await chat_module._rebuild_outdated_bm25_index(db, store, doc_id)

    assert not store._index_path(doc_id).exists()


def test_delete_forgets_the_current_verdict(tmp_path: Path) -> None:
    store = BM25Retriever(base_dir=tmp_path)
    store.index_document(3, [1], ["x"], "tr")
    assert store.is_current(3)

    store.delete_index(3)
    _write_legacy_index(store, 3, [1])

    assert store.is_current(3) is False


async def test_rebuild_post_check_waits_for_the_map_lock(
    session_maker: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc_id, chunk_ids = await _add_document(session_maker)
    store = BM25Retriever(base_dir=tmp_path / "bm25")
    _write_legacy_index(store, doc_id, chunk_ids)
    waiting = asyncio.Event()
    real_lock = document_anon_store.document_map_lock

    @contextlib.asynccontextmanager
    async def observed_lock(document_id: int):
        waiting.set()
        async with real_lock(document_id) as handle:
            yield handle

    monkeypatch.setattr(chat_module, "document_map_lock", observed_lock)

    async with session_maker() as db:
        async with real_lock(doc_id):
            task = asyncio.create_task(
                chat_module._rebuild_outdated_bm25_index(db, store, doc_id)
            )
            await asyncio.wait_for(waiting.wait(), timeout=5)
            assert not task.done()
            with sqlite3.connect(tmp_path / "bm25.db") as conn:
                conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        await task

    assert not store._index_path(doc_id).exists()


async def test_rebuild_cancelled_mid_write_leaves_no_index(
    session_maker: Any, tmp_path: Path
) -> None:
    doc_id, chunk_ids = await _add_document(session_maker)
    store = BM25Retriever(base_dir=tmp_path / "bm25")
    _write_legacy_index(store, doc_id, chunk_ids)
    path = store._index_path(doc_id)
    started, release, written = threading.Event(), threading.Event(), threading.Event()
    write = store.index_document

    def held_write(*args: Any) -> None:
        started.set()
        release.wait(5)
        write(*args)
        written.set()

    store.index_document = held_write

    async with session_maker() as db:
        task = asyncio.create_task(chat_module._rebuild_outdated_bm25_index(db, store, doc_id))
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert path.exists()
        release.set()
        assert await asyncio.to_thread(written.wait, 5)
        for _ in range(500):
            if not path.exists():
                break
            await asyncio.sleep(0.01)

    assert not path.exists()


async def test_discarded_index_is_built_again_on_the_next_retrieval(
    session_maker: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc_id, chunk_ids = await _add_document(session_maker)
    store = BM25Retriever(base_dir=tmp_path / "bm25")
    store.index_document(doc_id, chunk_ids, ["a", "b"], "tr")
    store.delete_index(doc_id)

    class _NoVectors:
        def hybrid_search(self, **kwargs: Any) -> list:
            return []

    monkeypatch.setattr(chat_module, "_get_bm25_retriever", lambda: store)
    monkeypatch.setattr(chat_module, "_get_vector_store", lambda: _NoVectors())

    async with session_maker() as db:
        await chat_module._retrieve_chunks(db, doc_id, "işveren", top_k=5, chunk_count=2)

    data = BM25Retriever(base_dir=tmp_path / "bm25")._load_index(doc_id)
    assert (data["tokenizer_version"], data["chunk_ids"]) == (TOKENIZER_VERSION, chunk_ids)


async def test_rebuild_removes_its_index_when_reused_ids_hold_new_text(
    session_maker: Any, tmp_path: Path
) -> None:
    doc_id, chunk_ids = await _add_document(session_maker)
    store = BM25Retriever(base_dir=tmp_path / "bm25")
    _write_legacy_index(store, doc_id, chunk_ids)
    write = store.index_document

    def write_then_reprocess_with_the_same_ids(*args: Any) -> None:
        write(*args)
        with sqlite3.connect(tmp_path / "bm25.db") as conn:
            conn.execute(
                "UPDATE chunks SET raw_text = 'reprocessed', sanitized_text = 'reprocessed'"
                " WHERE id = ?",
                (chunk_ids[0],),
            )

    store.index_document = write_then_reprocess_with_the_same_ids

    async with session_maker() as db:
        await chat_module._rebuild_outdated_bm25_index(db, store, doc_id)

    assert not store._index_path(doc_id).exists()


async def test_write_failing_after_a_cancel_is_logged_by_class(
    session_maker: Any, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    doc_id, chunk_ids = await _add_document(session_maker)
    store = BM25Retriever(base_dir=tmp_path / "bm25")
    _write_legacy_index(store, doc_id, chunk_ids)
    started, release, done = threading.Event(), threading.Event(), threading.Event()

    def failing_write(*args: Any) -> None:
        started.set()
        release.wait(5)
        done.set()
        raise OSError(f"disk full at {tmp_path}")

    store.index_document = failing_write

    with caplog.at_level("WARNING"):
        async with session_maker() as db:
            task = asyncio.create_task(chat_module._rebuild_outdated_bm25_index(db, store, doc_id))
            assert await asyncio.to_thread(started.wait, 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            release.set()
            assert await asyncio.to_thread(done.wait, 5)
            for _ in range(500):
                if "failed: OSError" in caplog.text:
                    break
                await asyncio.sleep(0.01)

    assert f"BM25 index of document_id={doc_id} failed: OSError" in caplog.text
    assert str(tmp_path) not in caplog.text


def _replace_after_write(store: BM25Retriever, db_path: Path, statements: list[tuple]) -> None:
    """Make ``store``'s next write run ``statements`` on the database right after it lands."""
    write = store.index_document

    def write_then_change(*args: Any) -> None:
        write(*args)
        with sqlite3.connect(db_path) as conn:
            for sql, params in statements:
                conn.execute(sql, params)

    store.index_document = write_then_change


async def test_rebuild_removes_its_index_when_a_chunk_was_removed_meanwhile(
    session_maker: Any, tmp_path: Path
) -> None:
    doc_id, chunk_ids = await _add_document(session_maker)
    store = BM25Retriever(base_dir=tmp_path / "bm25")
    _write_legacy_index(store, doc_id, chunk_ids)
    _replace_after_write(
        store, tmp_path / "bm25.db", [("DELETE FROM chunks WHERE id = ?", (chunk_ids[1],))]
    )

    async with session_maker() as db:
        await chat_module._rebuild_outdated_bm25_index(db, store, doc_id)

    assert not store._index_path(doc_id).exists()


async def test_rebuild_removes_its_index_when_chunks_were_replaced_one_for_one(
    session_maker: Any, tmp_path: Path
) -> None:
    doc_id, chunk_ids = await _add_document(session_maker)
    store = BM25Retriever(base_dir=tmp_path / "bm25")
    _write_legacy_index(store, doc_id, chunk_ids)
    _replace_after_write(
        store,
        tmp_path / "bm25.db",
        [
            ("DELETE FROM chunks WHERE id = ?", (chunk_ids[-1],)),
            (
                "INSERT INTO chunks (document_id, \"index\", sanitized_text, char_count, chunk_type)"
                " VALUES (?, 1, 'new', 3, 'text')",
                (doc_id,),
            ),
        ],
    )

    async with session_maker() as db:
        await chat_module._rebuild_outdated_bm25_index(db, store, doc_id)

    assert not store._index_path(doc_id).exists()


async def test_failed_write_keeps_the_old_index(session_maker: Any, tmp_path: Path) -> None:
    doc_id, chunk_ids = await _add_document(session_maker)
    store = BM25Retriever(base_dir=tmp_path / "bm25")
    _write_legacy_index(store, doc_id, chunk_ids)
    before = store._index_path(doc_id).read_bytes()

    def failing_write(*args: Any) -> None:
        raise OSError("disk full")

    store.index_document = failing_write

    with pytest.raises(OSError, match="disk full"):
        async with session_maker() as db:
            await chat_module._rebuild_outdated_bm25_index(db, store, doc_id)

    assert store._index_path(doc_id).read_bytes() == before


async def test_failed_cleanup_never_hides_the_original_error(
    session_maker: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc_id, chunk_ids = await _add_document(session_maker)
    store = BM25Retriever(base_dir=tmp_path / "bm25")
    _write_legacy_index(store, doc_id, chunk_ids)

    async def fail(*_: Any) -> bool:
        raise RuntimeError("lock timeout")

    def undeletable(document_id: int) -> None:
        raise PermissionError("read-only")

    monkeypatch.setattr(chat_module, "_rebuilt_index_still_valid", fail)
    store.delete_index = undeletable

    with pytest.raises(RuntimeError, match="lock timeout"):
        async with session_maker() as db:
            await chat_module._rebuild_outdated_bm25_index(db, store, doc_id)


def test_written_index_stays_current_for_this_instance(tmp_path: Path) -> None:
    store = BM25Retriever(base_dir=tmp_path)
    store.index_document(1, [1], ["metin"])
    _write_legacy_index(store, 1, [1])

    assert store.is_current(1) is True


def test_current_verdict_is_remembered(tmp_path: Path) -> None:
    store = BM25Retriever(base_dir=tmp_path)
    BM25Retriever(base_dir=tmp_path).index_document(1, [1], ["metin"])
    assert store.is_current(1) is True

    _write_legacy_index(store, 1, [1])

    assert store.is_current(1) is True


async def test_rebuild_removes_its_index_when_the_language_changed_meanwhile(
    session_maker: Any, tmp_path: Path
) -> None:
    doc_id, chunk_ids = await _add_document(session_maker)
    store = BM25Retriever(base_dir=tmp_path / "bm25")
    _write_legacy_index(store, doc_id, chunk_ids)
    _replace_after_write(
        store,
        tmp_path / "bm25.db",
        [("UPDATE documents SET language_override = 'en' WHERE id = ?", (doc_id,))],
    )

    async with session_maker() as db:
        await chat_module._rebuild_outdated_bm25_index(db, store, doc_id)

    assert not store._index_path(doc_id).exists()


async def test_outdated_index_of_a_document_without_chunks_is_left_as_it_is(
    session_maker: Any, tmp_path: Path
) -> None:
    doc_id, chunk_ids = await _add_document(session_maker)
    with sqlite3.connect(tmp_path / "bm25.db") as conn:
        conn.execute("DELETE FROM chunks WHERE document_id = ?", (doc_id,))
    store = BM25Retriever(base_dir=tmp_path / "bm25")
    _write_legacy_index(store, doc_id, chunk_ids)
    before = store._index_path(doc_id).read_bytes()

    async with session_maker() as db:
        await chat_module._rebuild_outdated_bm25_index(db, store, doc_id)

    assert store._index_path(doc_id).read_bytes() == before


def test_cached_verdict_notices_an_index_deleted_elsewhere(tmp_path: Path) -> None:
    store = BM25Retriever(base_dir=tmp_path)
    store.index_document(1, [1], ["metin"], "tr")
    assert store.is_current(1) is True

    BM25Retriever(base_dir=tmp_path).delete_index(1)

    assert store.is_current(1) is False
