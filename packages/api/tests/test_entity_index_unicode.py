"""Unicode-aware tokens for the cross-document entity index.

Compound values (locations, organisations, addresses) are indexed per
token as well as whole. The tokenizer must work in every script: letters
outside ASCII keep their words intact, unspaced Han and Kana text yields
overlapping bigrams, and the dotted capital ``İ`` folds like ``I``.
"""

from __future__ import annotations

from typing import AsyncIterator

import pytest
from septum_core.anonymization_map import AnonymizationMap
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from septum_api.models import Base
from septum_api.models.document import Document
from septum_api.models.document_relationship import DocumentRelationship
from septum_api.models.entity_index import EntityIndex
from septum_api.services import entity_index_refresh
from septum_api.services.entity_index_service import (
    ENTITY_INDEX_VERSION,
    _normalize_value,
    _tokenize_for_index,
    collect_entities_from_anon_map,
)

_KEY = b"\x00" * 32


def _tokens(value: str) -> list[str]:
    return _tokenize_for_index(_normalize_value(value))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("München Hauptbahnhof", ["münchen", "hauptbahnhof"]),
        ("São Paulo", ["são", "paulo"]),
        ("Москва, Тверская улица", ["москва", "тверская", "улица"]),
        ("Αθήνα Κέντρο", ["αθήνα", "κέντρο"]),
        ("서울 강남구 테헤란로", ["서울", "강남구", "테헤란로"]),
        ("東京都新宿区", ["東京", "京都", "都新", "新宿", "宿区"]),
        ("新宿", ["新宿"]),
        ("«Acme» Holding", ["acme", "holding"]),
    ],
)
def test_tokens_keep_words_in_every_script(value: str, expected: list[str]) -> None:
    assert _tokens(value) == expected


def test_single_han_character_is_not_a_token() -> None:
    assert _tokens("区 Lara") == ["lara"]


def test_short_latin_tokens_and_stopwords_are_still_dropped() -> None:
    assert _tokens("Lara Cad. No 47 Kat 5 Antalya") == ["lara", "antalya"]


@pytest.mark.parametrize("variant", ["ISTANBUL", "istanbul", "İstanbul", "İSTANBUL"])
def test_dotted_capital_folds_like_plain_i(variant: str) -> None:
    assert _normalize_value(variant) == "istanbul"


def test_decomposed_input_normalizes_like_composed() -> None:
    import unicodedata

    composed = "Şişli Çarşı"
    assert _normalize_value(unicodedata.normalize("NFD", composed)) == _normalize_value(composed)


def _hashes(entity_type: str, value: str) -> set[str]:
    amap = AnonymizationMap(document_id=1, language="ja")
    amap.entity_map = {value: f"[{entity_type}_1]"}
    return {vh for _, vh, _ in collect_entities_from_anon_map(amap)}


def test_unspaced_cjk_addresses_share_a_bigram(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "septum_api.services.entity_index_service.get_encryption_key", lambda: _KEY
    )
    assert _hashes("POSTAL_ADDRESS", "東京都新宿区西新宿") & _hashes("LOCATION", "新宿区")


def test_cjk_person_names_stay_whole(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "septum_api.services.entity_index_service.get_encryption_key", lambda: _KEY
    )
    assert len(_hashes("PERSON_NAME", "山田太郎")) == 1


# ---------------------------------------------------------------- reindex


@pytest.fixture
async def maker(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[async_sessionmaker]:
    monkeypatch.setattr(
        "septum_api.services.entity_index_service.get_encryption_key", lambda: _KEY
    )
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _add_document(
    db: AsyncSession, doc_id: int, status: str = "completed", version: int | None = None
) -> None:
    db.add(
        Document(
            id=doc_id, filename=f"d{doc_id}", original_filename=f"d{doc_id}.pdf",
            file_type="application/pdf", file_format="pdf", detected_language="ja",
            encrypted_path=f"/x/{doc_id}", file_size_bytes=1, ingestion_status=status,
            entity_index_version=version,
        )
    )
    await db.commit()


def _map(doc_id: int, value: str) -> AnonymizationMap:
    amap = AnonymizationMap(document_id=doc_id, language="ja")
    amap.entity_map = {value: "[LOCATION_1]"}
    return amap


async def test_reindex_rebuilds_stale_documents_and_links_them(
    maker: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    maps = {1: _map(1, "東京都新宿区"), 2: _map(2, "新宿区西新宿"), 3: _map(3, "新宿区")}

    async def _get_map(document_id: int):
        return maps.get(document_id)

    monkeypatch.setattr(entity_index_refresh, "get_document_map", _get_map)
    async with maker() as db:
        await _add_document(db, 1)
        await _add_document(db, 2, version=ENTITY_INDEX_VERSION - 1)
        await _add_document(db, 3, status="processing")
        db.add(EntityIndex(document_id=1, entity_type="LOCATION", value_hash="old", occurrences=1))
        await db.commit()

    rebuilt = await entity_index_refresh.reindex_stale_documents(maker)

    async with maker() as db:
        hashes = (await db.execute(select(EntityIndex.value_hash))).scalars().all()
        versions = dict((await db.execute(select(Document.id, Document.entity_index_version))).all())
        pairs = (
            await db.execute(select(DocumentRelationship.doc_a_id, DocumentRelationship.doc_b_id))
        ).all()
    assert rebuilt == 2
    assert "old" not in hashes
    assert versions == {1: ENTITY_INDEX_VERSION, 2: ENTITY_INDEX_VERSION, 3: None}
    assert pairs == [(1, 2)]


async def test_reindex_leaves_current_and_mapless_documents_alone(
    maker: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _no_map(document_id: int):
        return None

    monkeypatch.setattr(entity_index_refresh, "get_document_map", _no_map)
    async with maker() as db:
        await _add_document(db, 1, version=ENTITY_INDEX_VERSION)
        await _add_document(db, 2)
        db.add(EntityIndex(document_id=1, entity_type="LOCATION", value_hash="kept", occurrences=1))
        await db.commit()

    rebuilt = await entity_index_refresh.reindex_stale_documents(maker)

    async with maker() as db:
        hashes = (await db.execute(select(EntityIndex.value_hash))).scalars().all()
        versions = dict((await db.execute(select(Document.id, Document.entity_index_version))).all())
    assert rebuilt == 0
    assert hashes == ["kept"]
    assert versions == {1: ENTITY_INDEX_VERSION, 2: None}


async def test_refresh_after_ingestion_reports_whether_rows_were_written(
    maker: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    from septum_api.services import document_pipeline as dp

    async with maker() as db:
        await _add_document(db, 1, status="processing")
        document = await db.get(Document, 1)
        assert await dp._refresh_entity_index(
            db, document, _map(1, "新宿区"), run_token=None
        ) is True

    async def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(dp, "replace_index_for_document", _boom)
    async with maker() as db:
        document = await db.get(Document, 1)
        assert await dp._refresh_entity_index(
            db, document, _map(1, "新宿区"), run_token=None
        ) is False
