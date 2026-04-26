from __future__ import annotations

"""Tests for the cross-document entity index service.

The service is the data backbone of the entity-aware RAG routing and
the document relationship graph: it converts a per-document
:class:`AnonymizationMap` into hashed lookup rows, and it computes
weighted shared-entity scores between documents. The tests run against
an in-memory SQLite engine to keep the suite hermetic.
"""

import json
from typing import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from septum_api.models import Base
# Cross-table foreign keys (entity_detections → audit_events, etc.)
# require every referenced model class to be imported so SQLAlchemy can
# attach it to the shared ``Base.metadata`` before ``create_all`` runs.
# Listing them explicitly keeps the test independent of database.py.
from septum_api.models.audit_event import AuditEvent  # noqa: F401
from septum_api.models.document import Chunk, Document  # noqa: F401
from septum_api.models.document_relationship import DocumentRelationship
from septum_api.models.entity_detection import EntityDetection  # noqa: F401
from septum_api.models.entity_index import EntityIndex
from septum_api.models.user import User  # noqa: F401
from septum_api.services.entity_index_service import (
    ENTITY_UNIQUENESS_WEIGHTS,
    collect_entities_from_anon_map,
    find_documents_for_query_entities,
    hash_entity_value,
    recompute_relationships_for_document,
    replace_index_for_document,
)
from septum_core.anonymization_map import AnonymizationMap


@pytest.fixture
async def session(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncSession]:
    """In-memory SQLite session with schema and a deterministic HMAC key."""
    monkeypatch.setattr(
        "septum_api.services.entity_index_service.get_encryption_key",
        lambda: b"\x00" * 32,
    )
    # Keep all tables in the same in-memory database across connections by
    # using a shared cache URI.
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        yield s
    await engine.dispose()


def _build_anon_map(
    document_id: int, originals: dict[str, str]
) -> AnonymizationMap:
    m = AnonymizationMap(document_id=document_id, language="tr")
    m.entity_map = dict(originals)
    return m


async def _seed_document(db: AsyncSession, doc_id: int, name: str) -> None:
    doc = Document(
        id=doc_id,
        filename=name,
        original_filename=name,
        file_type="application/pdf",
        file_format="pdf",
        detected_language="tr",
        encrypted_path=f"/tmp/{name}.enc",
        ingestion_status="completed",
        chunk_count=0,
        entity_count=0,
        file_size_bytes=0,
    )
    db.add(doc)
    await db.commit()


def test_hash_is_deterministic_under_same_key() -> None:
    """The same value under the same key must produce the same hash."""
    key = b"\x42" * 32
    h1 = hash_entity_value("Ahmet Çelik", key=key)
    h2 = hash_entity_value("Ahmet Çelik", key=key)
    h3 = hash_entity_value("Ahmet Çelik", key=b"\x99" * 32)
    assert h1 == h2
    assert h1 != h3


def test_collect_entities_skips_unparseable_placeholders() -> None:
    """Entries whose placeholder doesn't match ``[TYPE_N]`` must be
    silently skipped — defensive handling against legacy / corrupted maps."""
    am = _build_anon_map(
        1,
        {
            "Ahmet Çelik": "[PERSON_NAME_1]",
            "TR940006200010100007123456": "[IBAN_1]",
            # Legacy / corrupt entries:
            "stray-value": "BARE_PLACEHOLDER",
            "another": "",
        },
    )
    entities = collect_entities_from_anon_map(am)
    types = sorted(t for t, _, _ in entities)
    assert types == ["IBAN", "PERSON_NAME"]


@pytest.mark.asyncio
async def test_replace_index_for_document_persists_unique_rows(
    session: AsyncSession,
) -> None:
    """Repeated populate calls must replace the previous rows."""
    await _seed_document(session, 1, "doc1.pdf")
    am1 = _build_anon_map(
        1,
        {"Ahmet Çelik": "[PERSON_NAME_1]", "TR940006200010100007123456": "[IBAN_1]"},
    )
    written = await replace_index_for_document(session, 1, am1)
    await session.commit()
    assert written == 2

    # A second populate with a smaller map must replace, not append.
    am2 = _build_anon_map(1, {"Ahmet Çelik": "[PERSON_NAME_1]"})
    written = await replace_index_for_document(session, 1, am2)
    await session.commit()
    assert written == 1

    rows = (
        (await session.execute(EntityIndex.__table__.select())).all()
    )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_find_documents_aggregates_scores_with_weights(
    session: AsyncSession,
) -> None:
    """A high-uniqueness shared entity must outweigh a low-uniqueness one."""
    await _seed_document(session, 10, "a.pdf")
    await _seed_document(session, 20, "b.pdf")
    await replace_index_for_document(
        session,
        10,
        _build_anon_map(
            10,
            {
                "Ahmet Çelik": "[PERSON_NAME_1]",
                "Antalya": "[LOCATION_1]",
            },
        ),
    )
    await replace_index_for_document(
        session,
        20,
        _build_anon_map(
            20,
            {
                "TR940006200010100007123456": "[IBAN_1]",
                "Antalya": "[LOCATION_1]",
            },
        ),
    )
    await session.commit()

    # Query mentions both Antalya (very common, low weight) and the IBAN
    # (globally unique, full weight). Doc 20 should dominate the ranking.
    scores = await find_documents_for_query_entities(
        session,
        [
            ("Antalya", "LOCATION"),
            ("TR940006200010100007123456", "IBAN"),
        ],
    )

    assert scores[10] == pytest.approx(ENTITY_UNIQUENESS_WEIGHTS["LOCATION"])
    assert scores[20] == pytest.approx(
        ENTITY_UNIQUENESS_WEIGHTS["LOCATION"]
        + ENTITY_UNIQUENESS_WEIGHTS["IBAN"]
    )
    assert scores[20] > scores[10]


@pytest.mark.asyncio
async def test_recompute_relationships_writes_one_row_per_pair(
    session: AsyncSession,
) -> None:
    """Each (doc_a, doc_b) pair must be stored once with normalised order."""
    await _seed_document(session, 1, "a.pdf")
    await _seed_document(session, 2, "b.pdf")
    await _seed_document(session, 3, "c.pdf")

    # Doc 1 and Doc 2 share an IBAN (strong link, weight 1.0).
    # Doc 1 and Doc 3 share only a LOCATION (weak link, weight 0.1).
    await replace_index_for_document(
        session,
        1,
        _build_anon_map(
            1,
            {
                "TR940006200010100007123456": "[IBAN_1]",
                "Antalya": "[LOCATION_1]",
                "Ahmet Çelik": "[PERSON_NAME_1]",
            },
        ),
    )
    await replace_index_for_document(
        session,
        2,
        _build_anon_map(
            2,
            {"TR940006200010100007123456": "[IBAN_1]"},
        ),
    )
    await replace_index_for_document(
        session,
        3,
        _build_anon_map(
            3,
            {"Antalya": "[LOCATION_1]"},
        ),
    )
    await session.commit()

    written = await recompute_relationships_for_document(session, 1)
    await session.commit()
    assert written == 2

    rows = (
        await session.execute(DocumentRelationship.__table__.select())
    ).all()
    pairs = {
        (r.doc_a_id, r.doc_b_id): (r.score, r.shared_entity_count, r.shared_entity_types)
        for r in rows
    }
    # Doc1↔Doc2 must have score == IBAN weight; Doc1↔Doc3 == LOCATION weight.
    assert pairs[(1, 2)][0] == pytest.approx(ENTITY_UNIQUENESS_WEIGHTS["IBAN"])
    assert pairs[(1, 2)][1] == 1
    assert json.loads(pairs[(1, 2)][2]) == {"IBAN": 1}
    assert pairs[(1, 3)][0] == pytest.approx(ENTITY_UNIQUENESS_WEIGHTS["LOCATION"])
    assert pairs[(1, 3)][1] == 1
    assert json.loads(pairs[(1, 3)][2]) == {"LOCATION": 1}


def test_collect_emits_per_token_hashes_for_compound_locations() -> None:
    """A compound LOCATION span must emit hashes for each content token
    in addition to the full-span hash, so two documents that wrap the
    same city in different surrounding text still link up via the
    shared token."""
    am = _build_anon_map(
        1,
        {
            "Muratpaşa / ANTALYA": "[LOCATION_1]",
        },
    )
    rows = collect_entities_from_anon_map(am)
    types = sorted(t for t, _, _ in rows)
    # Three rows: full hash + "muratpaşa" + "antalya".
    assert types == ["LOCATION", "LOCATION", "LOCATION"]
    assert len({vh for _, vh, _ in rows}) == 3


def test_collect_does_not_tokenize_person_names() -> None:
    """PERSON_NAME entities must NEVER be split into per-token hashes
    or two different people who share a surname token would collapse
    into one logical entity. This is the regression that prevents an
    "Ahmet Çelik" question from being routed to a "Mehmet Çelik" doc."""
    am = _build_anon_map(
        1,
        {"Ahmet Çelik": "[PERSON_NAME_1]"},
    )
    rows = collect_entities_from_anon_map(am)
    # Exactly one row — only the full-span hash.
    assert len(rows) == 1
    assert rows[0][0] == "PERSON_NAME"


def test_collect_does_not_tokenize_unique_identifiers() -> None:
    """IBAN / NATIONAL_ID / TAX_ID stay full-span only — they ARE the
    atom and tokenisation would only produce noise."""
    am = _build_anon_map(
        9,
        {
            "TR940006200010100007123456": "[IBAN_1]",
            "12345678901": "[NATIONAL_ID_1]",
            "5312984760": "[TAX_ID_1]",
        },
    )
    rows = collect_entities_from_anon_map(am)
    types = sorted(t for t, _, _ in rows)
    assert types == ["IBAN", "NATIONAL_ID", "TAX_ID"]


def test_collect_normalisation_unifies_casing_and_punctuation() -> None:
    """Two surface forms of the same value must hash to the same row
    (e.g. "ANTALYA" and "Antalya," and "antalya")."""
    secret = b"\x00" * 32
    h_upper = hash_entity_value("ANTALYA", key=secret)
    h_lower = hash_entity_value("antalya", key=secret)
    h_punct = hash_entity_value("Antalya,", key=secret)
    # NOTE: hash_entity_value does NOT normalise on its own — callers
    # use ``_hashes_for_entity`` which normalises first. So we test
    # the normalisation pipeline through the public collector.
    am1 = _build_anon_map(1, {"ANTALYA": "[LOCATION_1]"})
    am2 = _build_anon_map(2, {"antalya,": "[LOCATION_1]"})
    rows1 = collect_entities_from_anon_map(am1)
    rows2 = collect_entities_from_anon_map(am2)
    hashes1 = {vh for _, vh, _ in rows1}
    hashes2 = {vh for _, vh, _ in rows2}
    assert hashes1 == hashes2
    # And the raw helper outputs are still distinct (proves the
    # normalisation lives at the higher layer, not in the hash itself).
    assert h_upper != h_lower
    assert h_lower != h_punct


@pytest.mark.asyncio
async def test_replace_index_handles_same_token_under_different_types(
    session: AsyncSession,
) -> None:
    """A document containing the same token across two entity types
    (e.g. "ANTALYA" inside both a LOCATION span and an
    ORGANIZATION_NAME span) must NOT raise a UNIQUE constraint
    violation. Both rows share value_hash + document_id but differ on
    entity_type — the constraint key has to include entity_type or the
    second insert blows up the whole ingestion transaction."""
    await _seed_document(session, 100, "kvkk_form.pdf")
    am = AnonymizationMap(document_id=100, language="tr")
    am.entity_map = {
        "Muratpaşa / ANTALYA": "[LOCATION_1]",
        "ANTALYA SAĞLIK MERKEZİ": "[ORGANIZATION_NAME_1]",
    }
    written = await replace_index_for_document(session, 100, am)
    await session.commit()

    rows = (
        await session.execute(EntityIndex.__table__.select())
    ).all()
    # Each entity emits the full hash + tokens; "antalya" appears in
    # both, so we expect at least one row pair where value_hash matches
    # across LOCATION and ORGANIZATION_NAME — the key fact is that the
    # commit succeeded with a non-zero row count.
    assert written > 0
    by_hash: dict[str, set[str]] = {}
    for row in rows:
        by_hash.setdefault(row.value_hash, set()).add(row.entity_type)
    shared = [types for types in by_hash.values() if len(types) > 1]
    assert any("LOCATION" in s and "ORGANIZATION_NAME" in s for s in shared)


@pytest.mark.asyncio
async def test_token_level_hashing_links_address_variants(
    session: AsyncSession,
) -> None:
    """Two documents with different surrounding address text but a
    shared city core must end up linked via at least one shared
    LOCATION value_hash row."""
    await _seed_document(session, 1, "contract.pdf")
    await _seed_document(session, 2, "kvkk_form.pdf")
    await replace_index_for_document(
        session,
        1,
        _build_anon_map(
            1,
            {"Lara Caddesi No:47 Kat:5 Muratpaşa / ANTALYA": "[POSTAL_ADDRESS_1]"},
        ),
    )
    await replace_index_for_document(
        session,
        2,
        _build_anon_map(
            2,
            {"Güllük Cad. Panorama İş Merkezi No:3 Muratpaşa / ANTALYA": "[POSTAL_ADDRESS_1]"},
        ),
    )
    await session.commit()

    written = await recompute_relationships_for_document(session, 1)
    await session.commit()
    assert written == 1, "expected one pair row linking the two addresses"


@pytest.mark.asyncio
async def test_recompute_relationships_replaces_prior_rows(
    session: AsyncSession,
) -> None:
    """Re-running the recompute must not duplicate existing pair rows."""
    await _seed_document(session, 1, "a.pdf")
    await _seed_document(session, 2, "b.pdf")
    await replace_index_for_document(
        session,
        1,
        _build_anon_map(1, {"Ahmet Çelik": "[PERSON_NAME_1]"}),
    )
    await replace_index_for_document(
        session,
        2,
        _build_anon_map(2, {"Ahmet Çelik": "[PERSON_NAME_1]"}),
    )
    await session.commit()

    await recompute_relationships_for_document(session, 1)
    await session.commit()
    await recompute_relationships_for_document(session, 1)
    await session.commit()

    rows = (
        await session.execute(DocumentRelationship.__table__.select())
    ).all()
    assert len(rows) == 1
