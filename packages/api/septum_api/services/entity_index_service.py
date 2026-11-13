from __future__ import annotations

"""Entity index population and lookup for cross-document chat routing.

Every entity originally detected in a document is keyed under a
deterministic HMAC-SHA256 of the original value taken under the local
encryption key. The hash means two documents that mention the same
person / IBAN / national ID resolve to the same index row even though
the per-document anonymization maps assign different placeholders, but
a leak of the index alone never reveals the original PII because the
key never leaves the air-gapped zone.

The service drives two privacy-aware features:

* Chat-time RAG narrowing — when the user asks about a specific
  entity, retrieval is scoped to only the documents that actually
  contain that entity.
* Document relationship graph — pairwise scores power the visual
  cluster view and the auto-cluster suggestion in chat.
"""

import hashlib
import hmac
import json
import logging
import re
import unicodedata
from collections import defaultdict
from itertools import groupby
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

from septum_core.anonymization_map import AnonymizationMap
from septum_core.text_utils import lower_preserving_offsets, normalize_unicode
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..models.document_relationship import DocumentRelationship
from ..models.entity_index import EntityIndex
from ..utils.crypto import get_encryption_key

logger = logging.getLogger(__name__)

_PLACEHOLDER_RE = re.compile(r"^\[([A-Z][A-Z_]*)_(\d+)\]$")

# Version of the hashing scheme below (value normalization and tokens).
# Every document records the version its index rows were written under;
# rows written under an older one are rebuilt from the document's map
# (see ``entity_index_refresh``). Bump it whenever a stored hash would
# change for the same value.
ENTITY_INDEX_VERSION: int = 2

# Entity types whose detected spans are routinely **compound** — long
# strings like "Muratpaşa / ANTALYA" or "Lara Caddesi No:47 Kat:5 …
# Muratpaşa / ANTALYA" where two documents almost never carry an
# identical full span even though they reference the same underlying
# city or company. For these we emit per-token hashes IN ADDITION to
# the full-span hash so cross-document overlap (the shared "antalya"
# token in the example) is detectable. Atomic / identity-bearing
# types (PERSON_NAME, NATIONAL_ID, IBAN, …) deliberately stay
# full-span-only so two different people who happen to share a
# surname token are not collapsed into one entity by the index.
_TOKENIZABLE_ENTITY_TYPES: frozenset[str] = frozenset(
    {"LOCATION", "ORGANIZATION_NAME", "POSTAL_ADDRESS"}
)

# Han and Kana are written without spaces, so their runs are split into
# overlapping bigrams ("東京都" -> "東京", "京都"). Hangul is spaced like
# Latin, but its syllable blocks pack two or three letters each, so a
# two-syllable word already identifies a place. Every other letter, mark or
# digit run is a word, kept from three characters up.
_BIGRAM_RANGES: tuple[tuple[int, int], ...] = (
    (0x3040, 0x30FF), (0x31F0, 0x31FF), (0x3400, 0x4DBF), (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF), (0xFF66, 0xFF9F), (0x20000, 0x323AF),
)
_HANGUL_RANGES: tuple[tuple[int, int], ...] = (
    (0x1100, 0x11FF), (0x3130, 0x318F), (0xA960, 0xA97F), (0xAC00, 0xD7AF),
)
_MIN_TOKEN_LENGTH: Dict[str, int] = {"word": 3, "hangul": 2}

# Function-word and structural-marker tokens that should never act as
# an entity-overlap signal. Address/organisation spans routinely carry
# these (``no``, ``kat``, ``cad``, ``mh``…) and they would otherwise
# inflate the relationship score with meaningless cross-doc matches.
_TOKENIZE_STOPWORDS: frozenset[str] = frozenset(
    {
        "and", "ile", "için", "için.", "için,", "için:", "için;",
        "için-", "için_", "için(", "için)", "için[", "için]",
        "the",
        "cad", "cd", "sok", "sk", "sokak", "mah", "mh", "mahalle",
        "blok", "kat", "daire", "no", "apt", "apartmani",
        "merkezi", "merkez", "tic", "ltd", "sti", "san", "anonim",
        "sti.", "ltd.",
    }
)


# The relative weight assigned to each entity type when scoring how
# strongly two documents are linked by sharing it. Globally unique
# identifiers (IBAN, NATIONAL_ID, PASSPORT_NUMBER) carry full weight
# because two documents that share one effectively reference the same
# legal entity. Common signals (LOCATION, DATE_TIME) carry trace
# weight because city names and dates appear in unrelated documents
# all the time and would otherwise dominate the relationship graph.
ENTITY_UNIQUENESS_WEIGHTS: Dict[str, float] = {
    "NATIONAL_ID": 1.00,
    "PASSPORT_NUMBER": 1.00,
    "IBAN": 1.00,
    "TAX_ID": 1.00,
    "CREDIT_CARD_NUMBER": 1.00,
    "SOCIAL_SECURITY_NUMBER": 1.00,
    "DRIVERS_LICENSE": 1.00,
    "MEDICAL_RECORD_NUMBER": 0.95,
    "HEALTH_INSURANCE_ID": 0.95,
    "EMAIL_ADDRESS": 0.85,
    "PHONE_NUMBER": 0.80,
    "POSTAL_ADDRESS": 0.75,
    "DATE_OF_BIRTH": 0.40,
    "ORGANIZATION_NAME": 0.40,
    "PERSON_NAME": 0.30,
    "LOCATION": 0.10,
    "DATE_TIME": 0.05,
    "DATE": 0.05,
}

# Score thresholds for converting the weighted shared-entity sum into
# a categorical relationship strength. Tunable; production telemetry
# will likely refine these.
RELATIONSHIP_THRESHOLD_STRONG: float = 1.0
RELATIONSHIP_THRESHOLD_MEDIUM: float = 0.4


def hash_entity_value(value: str, key: bytes | None = None) -> str:
    """Return the HMAC-SHA256 hex digest for ``value`` under the local key."""
    secret = key if key is not None else get_encryption_key()
    return hmac.new(secret, value.encode("utf-8"), hashlib.sha256).hexdigest()


def _entity_type_from_placeholder(placeholder: str) -> str | None:
    """Return the entity type encoded in a placeholder like ``[PERSON_NAME_3]``."""
    match = _PLACEHOLDER_RE.match(placeholder)
    return match.group(1) if match else None


_PUNCT_STRIP_RE = re.compile(r"[\.,;:!?/\\\-\(\)\[\]\{\}\"'`]+")


def _is_separator(ch: str) -> bool:
    """ASCII separators above, plus any non-ASCII punctuation (« » 、 · ’)."""
    return not ch.isascii() and unicodedata.category(ch).startswith("P")


def _normalize_value(value: str) -> str:
    """Return the value used for hashing.

    NFC composition, lower-casing, punctuation stripping and whitespace
    collapsing unify the surface forms of an entity so two documents that
    wrote "ANTALYA" and "Antalya," (or "İSTANBUL" and "Istanbul") still
    resolve to the same hash. Lower-casing never changes the length, so
    the dotted capital ``İ`` becomes a plain ``i``. Identity information
    that the casing carries (acronyms, IBAN check digits) is preserved by
    the type-specific recognisers themselves; the cross-document index only
    needs the canonical representation.
    """
    lowered = lower_preserving_offsets(normalize_unicode(value.strip()))
    no_punct = _PUNCT_STRIP_RE.sub(" ", lowered)
    no_punct = "".join(" " if _is_separator(ch) else ch for ch in no_punct)
    return re.sub(r"\s+", " ", no_punct).strip()


def _in_ranges(code: int, ranges: tuple[tuple[int, int], ...]) -> bool:
    return any(lo <= code <= hi for lo, hi in ranges)


def _char_class(ch: str) -> Optional[str]:
    """``bigram``, ``hangul`` or ``word`` for a token character, else ``None``."""
    code = ord(ch)
    if _in_ranges(code, _BIGRAM_RANGES):
        return "bigram"
    if _in_ranges(code, _HANGUL_RANGES):
        return "hangul"
    if unicodedata.category(ch)[0] in "LMN":
        return "word"
    return None


def _run_tokens(kind: str, run: str) -> List[str]:
    """Tokens of one same-class character run."""
    if kind == "bigram":
        if len(run) < 2:
            return []
        return [run[i : i + 2] for i in range(len(run) - 1)]
    return [run] if len(run) >= _MIN_TOKEN_LENGTH[kind] else []


def _tokenize_for_index(normalized_value: str) -> List[str]:
    """Return the per-token hash candidates for a compound entity span.

    Letter, mark and digit runs of any script are words; unspaced Han and
    Kana runs become overlapping bigrams. Words shorter than 3 characters
    (2 for Hangul) and structural / function words (``no``, ``kat``,
    ``cad``…) are dropped — they would otherwise dominate the relationship
    signal with cross-document matches that carry no identifying
    information.
    """
    tokens: List[str] = []
    for kind, chars in groupby(normalized_value, key=_char_class):
        if kind is None:
            continue
        tokens.extend(_run_tokens(kind, "".join(chars)))
    return [token for token in tokens if token not in _TOKENIZE_STOPWORDS]


def _hashes_for_entity(
    entity_type: str,
    raw_value: str,
    secret: bytes,
) -> List[str]:
    """Return every value_hash an entity should be indexed under.

    Always emits the full-span hash so identity matches keep working.
    For ``_TOKENIZABLE_ENTITY_TYPES`` (LOCATION / ORGANIZATION_NAME /
    POSTAL_ADDRESS) also emits a hash per content token so two
    documents with different surrounding text but a shared core word
    (e.g. "ANTALYA") still link up. Atomic types (PERSON_NAME,
    NATIONAL_ID, IBAN, …) get only the full hash so two different
    people who happen to share a surname token are NOT collapsed.
    """
    normalized = _normalize_value(raw_value)
    if not normalized:
        return []
    hashes = [hash_entity_value(normalized, key=secret)]
    if entity_type in _TOKENIZABLE_ENTITY_TYPES:
        for token in _tokenize_for_index(normalized):
            if token == normalized:
                continue
            hashes.append(hash_entity_value(token, key=secret))
    # Dedup while preserving order so callers can iterate idempotently.
    seen: set[str] = set()
    unique: List[str] = []
    for h in hashes:
        if h not in seen:
            seen.add(h)
            unique.append(h)
    return unique


def collect_entities_from_anon_map(
    anon_map: AnonymizationMap,
) -> List[Tuple[str, str, int]]:
    """Return a list of ``(entity_type, value_hash, occurrences)`` tuples.

    Aggregates by ``(entity_type, value_hash)``. For tokenizable types
    each detected entity contributes both its full-span hash and one
    hash per content token, all under the same entity type, so cross-
    document overlap on a shared core word ("antalya" inside two
    different address strings) is detectable. Placeholders that do
    not parse to a known entity type are skipped.
    """
    secret = get_encryption_key()
    counts: Dict[Tuple[str, str], int] = defaultdict(int)
    for original, placeholder in anon_map.entity_map.items():
        entity_type = _entity_type_from_placeholder(placeholder)
        if entity_type is None or not original:
            continue
        for value_hash in _hashes_for_entity(entity_type, original, secret):
            counts[(entity_type, value_hash)] += 1
    return [(et, vh, n) for (et, vh), n in counts.items()]


async def replace_index_for_document(
    db: AsyncSession,
    document_id: int,
    anon_map: AnonymizationMap,
) -> int:
    """Re-populate :class:`EntityIndex` rows for a single document.

    Existing rows for the document are deleted and replaced atomically
    with whatever the current anon_map yields. Returns the number of
    new rows persisted. Caller is responsible for committing.
    """
    await db.execute(delete(EntityIndex).where(EntityIndex.document_id == document_id))
    entities = collect_entities_from_anon_map(anon_map)
    rows = [
        EntityIndex(
            document_id=document_id,
            entity_type=entity_type,
            value_hash=value_hash,
            occurrences=occurrences,
        )
        for entity_type, value_hash, occurrences in entities
    ]
    if rows:
        db.add_all(rows)
    return len(rows)


async def drop_index_for_document(db: AsyncSession, document_id: int) -> int:
    """Remove everything the entity index holds about one document.

    Deletes its :class:`EntityIndex` rows and the :class:`DocumentRelationship`
    rows where it is either side, and clears its ``entity_index_version`` so
    no later rebuild or lookup mistakes it for an indexed document. Safe to
    repeat. Returns the number of rows deleted. Caller is responsible for
    committing.
    """
    from ..models.document import Document

    index_result = await db.execute(
        delete(EntityIndex).where(EntityIndex.document_id == document_id)
    )
    pair_result = await db.execute(
        delete(DocumentRelationship).where(
            (DocumentRelationship.doc_a_id == document_id)
            | (DocumentRelationship.doc_b_id == document_id)
        )
    )
    await db.execute(
        update(Document)
        .where(Document.id == document_id)
        .values(entity_index_version=None)
    )
    return (index_result.rowcount or 0) + (pair_result.rowcount or 0)


async def _failed_ids_with_index(
    session_maker: async_sessionmaker[AsyncSession],
) -> List[int]:
    """Ids of ``failed`` documents that still hold an index version, rows or links."""
    from ..models.document import Document

    has_rows = select(EntityIndex.id).where(EntityIndex.document_id == Document.id)
    has_links = select(DocumentRelationship.id).where(
        (DocumentRelationship.doc_a_id == Document.id)
        | (DocumentRelationship.doc_b_id == Document.id)
    )
    async with session_maker() as db:
        rows = await db.execute(
            select(Document.id)
            .where(
                Document.ingestion_status == "failed",
                (Document.entity_index_version.is_not(None))
                | has_rows.exists()
                | has_links.exists(),
            )
            .order_by(Document.id)
        )
        return list(rows.scalars().all())


async def _purge_failed_batch(db: AsyncSession, document_ids: List[int]) -> int:
    """Drop the index of the documents in ``document_ids`` that are still ``failed``.

    The first statement is a status-guarded UPDATE ... RETURNING that clears
    ``entity_index_version``. It takes the row locks (a write lock on SQLite,
    row locks on PostgreSQL) and rechecks the status under them, so a document
    reprocessed since it was selected is skipped and one that is ``failed`` now
    cannot change status until this transaction ends; the deletes then act
    only on the returned ids. Caller commits.
    """
    from ..models.document import Document

    locked = await db.execute(
        update(Document)
        .where(Document.id.in_(document_ids), Document.ingestion_status == "failed")
        .values(entity_index_version=None)
        .returning(Document.id)
    )
    still_failed = list(locked.scalars().all())
    if not still_failed:
        return 0
    await db.execute(delete(EntityIndex).where(EntityIndex.document_id.in_(still_failed)))
    await db.execute(
        delete(DocumentRelationship).where(
            DocumentRelationship.doc_a_id.in_(still_failed)
            | DocumentRelationship.doc_b_id.in_(still_failed)
        )
    )
    return len(still_failed)


async def drop_index_of_failed_documents(
    session_maker: async_sessionmaker[AsyncSession], batch_size: int = 100
) -> int:
    """Drop the index of every ``failed`` document that still has one.

    A failed rebuild used to leave the document's previous index behind (see
    :func:`drop_index_for_document`); this clears what such runs left on an
    existing install. Only documents that still hold something are selected,
    so once cleaned the next startup does no work. Each batch is its own
    transaction and purges only documents still ``failed`` when it runs, so a
    document reprocessed meanwhile keeps its new index. Returns how many
    documents were purged; only that count is logged, never hashes.
    """
    document_ids = await _failed_ids_with_index(session_maker)
    purged = 0
    for start in range(0, len(document_ids), batch_size):
        async with session_maker() as db:
            purged += await _purge_failed_batch(db, document_ids[start : start + batch_size])
            await db.commit()
    if purged:
        logger.info("Dropped the stale entity index of %d failed documents", purged)
    return purged


async def find_documents_for_query_entities(
    db: AsyncSession,
    entities: Iterable[Tuple[str, str]],
    *,
    owner_id: Optional[int] = None,
) -> Dict[int, float]:
    """Return ``{document_id: weighted_score}`` for documents sharing at
    least one entity with ``entities``.

    ``owner_id`` scopes the auto-RAG match to a single user's documents
    so a query mentioning a name does not surface matched documents
    from other users' corpora. Pass ``None`` only for admin-context
    callers who explicitly need the cross-user view.
    """
    secret = get_encryption_key()
    seen_query_entities: set[Tuple[str, ...]] = set()
    scores: Dict[int, float] = defaultdict(float)
    for raw_value, entity_type in entities:
        if not entity_type or not raw_value:
            continue
        hashes = _hashes_for_entity(entity_type, raw_value, secret)
        if not hashes:
            continue
        signature = (entity_type, *hashes)
        if signature in seen_query_entities:
            continue
        seen_query_entities.add(signature)

        weight = ENTITY_UNIQUENESS_WEIGHTS.get(entity_type, 0.30)
        stmt = select(EntityIndex.document_id).where(
            EntityIndex.value_hash.in_(hashes),
            EntityIndex.entity_type == entity_type,
        )
        if owner_id is not None:
            from ..models.document import Document as _Document
            stmt = stmt.join(
                _Document, _Document.id == EntityIndex.document_id
            ).where(_Document.user_id == owner_id)
        result = await db.execute(stmt)
        for document_id in {row[0] for row in result.all()}:
            scores[document_id] += weight
    return dict(scores)


def _shared_entity_summary(
    a_entities: Mapping[Tuple[str, str], int],
    b_entities: Mapping[Tuple[str, str], int],
) -> Tuple[float, int, Dict[str, int]]:
    """Compute the weighted score and the per-type count of shared entities."""
    score = 0.0
    shared_count = 0
    by_type: Dict[str, int] = defaultdict(int)
    for key in a_entities:
        if key in b_entities:
            entity_type, _ = key
            weight = ENTITY_UNIQUENESS_WEIGHTS.get(entity_type, 0.30)
            score += weight
            shared_count += 1
            by_type[entity_type] += 1
    return score, shared_count, dict(by_type)


async def recompute_relationships_for_document(
    db: AsyncSession,
    document_id: int,
) -> int:
    """Recompute :class:`DocumentRelationship` rows that involve ``document_id``.

    Deletes every relationship row that references this document and then
    re-inserts the rows for every other document that currently shares
    at least one entity with it. Pair ordering is normalized so each
    pair is stored exactly once (smaller id first). Caller commits.
    Returns the number of pair rows persisted.
    """
    await db.execute(
        delete(DocumentRelationship).where(
            (DocumentRelationship.doc_a_id == document_id)
            | (DocumentRelationship.doc_b_id == document_id)
        )
    )

    this_rows = await db.execute(
        select(EntityIndex.entity_type, EntityIndex.value_hash, EntityIndex.occurrences).where(
            EntityIndex.document_id == document_id
        )
    )
    this_entities: Dict[Tuple[str, str], int] = {
        (et, vh): occ for et, vh, occ in this_rows.all()
    }
    if not this_entities:
        return 0

    # Find candidate documents that share at least one value_hash with us.
    value_hashes = {vh for (_, vh) in this_entities.keys()}
    if not value_hashes:
        return 0
    others_q = await db.execute(
        select(
            EntityIndex.document_id,
            EntityIndex.entity_type,
            EntityIndex.value_hash,
            EntityIndex.occurrences,
        ).where(
            EntityIndex.document_id != document_id,
            EntityIndex.value_hash.in_(value_hashes),
        )
    )
    others_entities: Dict[int, Dict[Tuple[str, str], int]] = defaultdict(dict)
    for doc_id, et, vh, occ in others_q.all():
        others_entities[doc_id][(et, vh)] = occ

    rows: List[DocumentRelationship] = []
    for other_doc_id, other_entities in others_entities.items():
        score, shared_count, by_type = _shared_entity_summary(
            this_entities, other_entities
        )
        if shared_count == 0:
            continue
        a_id, b_id = sorted([document_id, other_doc_id])
        rows.append(
            DocumentRelationship(
                doc_a_id=a_id,
                doc_b_id=b_id,
                score=score,
                shared_entity_count=shared_count,
                shared_entity_types=json.dumps(by_type, sort_keys=True),
            )
        )
    if rows:
        db.add_all(rows)
    return len(rows)


__all__ = [
    "ENTITY_INDEX_VERSION",
    "ENTITY_UNIQUENESS_WEIGHTS",
    "RELATIONSHIP_THRESHOLD_STRONG",
    "RELATIONSHIP_THRESHOLD_MEDIUM",
    "hash_entity_value",
    "collect_entities_from_anon_map",
    "replace_index_for_document",
    "drop_index_for_document",
    "drop_index_of_failed_documents",
    "find_documents_for_query_entities",
    "recompute_relationships_for_document",
]
