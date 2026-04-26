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
import re
from collections import defaultdict
from typing import Dict, Iterable, List, Mapping, Tuple

from septum_core.anonymization_map import AnonymizationMap
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.document_relationship import DocumentRelationship
from ..models.entity_index import EntityIndex
from ..utils.crypto import get_encryption_key

_PLACEHOLDER_RE = re.compile(r"^\[([A-Z][A-Z_]*)_(\d+)\]$")

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

_TOKEN_PATTERN = re.compile(r"[a-z0-9çğıöşü]{3,}", re.UNICODE)

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


def _normalize_value(value: str) -> str:
    """Return the value used for hashing.

    Lower-casing, punctuation stripping, and whitespace collapsing
    unify the surface forms of an entity so two documents that wrote
    "ANTALYA" and "Antalya," still resolve to the same hash. Identity
    information that the casing carries (acronyms, IBAN check digits)
    is preserved by the type-specific recognisers themselves; the
    cross-document index only needs the canonical representation.
    """
    lowered = value.strip().lower()
    no_punct = _PUNCT_STRIP_RE.sub(" ", lowered)
    return re.sub(r"\s+", " ", no_punct).strip()


def _tokenize_for_index(normalized_value: str) -> List[str]:
    """Return the per-token hash candidates for a compound entity span.

    Tokens shorter than 3 characters and structural / function words
    (``no``, ``kat``, ``cad``, ``ve``…) are dropped — they would
    otherwise dominate the relationship signal with cross-document
    matches that carry no identifying information.
    """
    return [
        token
        for token in _TOKEN_PATTERN.findall(normalized_value)
        if token not in _TOKENIZE_STOPWORDS
    ]


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


async def find_documents_for_query_entities(
    db: AsyncSession,
    entities: Iterable[Tuple[str, str]],
) -> Dict[int, float]:
    """Return ``{document_id: weighted_score}`` for every document
    sharing at least one entity with ``entities``.

    ``entities`` is an iterable of ``(original_value, entity_type)`` from
    the user's question. For tokenizable types each query entity is
    expanded into its full-span hash plus per-token hashes (mirroring
    the index side), and a document is awarded the entity's weight
    **once** if any of those hashes matches there — otherwise a
    multi-token match would inflate the score by the token count and
    routinely promote weak shared cities to strong-match status.
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
        # Dedup: a query that mentions the same canonical entity twice
        # should not double-score matching documents.
        signature = (entity_type, *hashes)
        if signature in seen_query_entities:
            continue
        seen_query_entities.add(signature)

        weight = ENTITY_UNIQUENESS_WEIGHTS.get(entity_type, 0.30)
        result = await db.execute(
            select(EntityIndex.document_id).where(
                EntityIndex.value_hash.in_(hashes),
                EntityIndex.entity_type == entity_type,
            )
        )
        # Each matching document earns this query entity's weight ONCE,
        # regardless of how many of the entity's hash variants matched
        # in that document.
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
    "ENTITY_UNIQUENESS_WEIGHTS",
    "RELATIONSHIP_THRESHOLD_STRONG",
    "RELATIONSHIP_THRESHOLD_MEDIUM",
    "hash_entity_value",
    "collect_entities_from_anon_map",
    "replace_index_for_document",
    "find_documents_for_query_entities",
    "recompute_relationships_for_document",
]
