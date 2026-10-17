"""PII gate for text that did not come from stored ``sanitized_text``.

Chunk text produced by ingestion is masked once and stored. Any other text
that can reach the cloud LLM — chunks edited in the approval modal,
client-supplied ``pre_approved_chunks``, inline chunk edits, user-typed
labels — is untrusted: it may carry raw PII the user typed. Such text goes
through :func:`gate_untrusted_texts` before it is used:

1. Known originals are replaced with the placeholders the anonymization map
   already assigned, so de-anonymization keeps working for them.
2. The sanitizer runs over the result to catch PII the map does not know
   yet. New entities are registered into the same map, so the answer can
   still be restored locally.

Placeholders the map already knows are never re-detected or rewritten;
placeholder-shaped text the map does not know is treated as ordinary text.
"""

from __future__ import annotations

import asyncio
import copy
import re
from typing import TYPE_CHECKING, Iterable, List, Optional, Sequence, Tuple

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.document import Chunk, Document
from ..models.settings import AppSettings
from ..utils.text_utils import normalize_unicode
from .anonymization_map import AnonymizationMap
from .document_anon_store import (
    document_map_lock,
    load_persisted_document_map,
    set_document_map,
)
from .sanitizer_factory import create_sanitizer

if TYPE_CHECKING:
    from .sanitizer import PIISanitizer

# Index digits are capped: real placeholder indexes are small, and an
# unbounded run of digits from untrusted text would make ``int()`` raise.
_PLACEHOLDER_RE = re.compile(r"\[([A-Z][A-Z_]*)_([0-9]{1,9})\]")
_SPAN_EDGE_CHARS = " \t\r\n.,;:!?()[]{}\"'"


def _known_placeholders(anon_map: AnonymizationMap) -> set[str]:
    """Return every placeholder the map can resolve or has handed out."""
    known = set(anon_map.entity_map.values())
    known.update(anon_map.placeholder_lookup.keys())
    known.update(anon_map.token_to_placeholder.values())
    known.discard("")
    return known


def reserve_placeholder_counters(
    anon_map: AnonymizationMap, extra_texts: Iterable[str] = ()
) -> None:
    """Raise ``token_counter`` above every placeholder index already in use.

    Maps built by the chat-time unification (and maps reconstructed after a
    lost key) carry placeholders but no counters; minting a new entity on
    such a map would otherwise reuse ``[PERSON_NAME_1]`` for a different
    person. ``extra_texts`` lets callers reserve indexes that only appear in
    stored text.
    """
    sources: List[str] = list(_known_placeholders(anon_map))
    sources.extend(t for t in extra_texts if t)
    for source in sources:
        for match in _PLACEHOLDER_RE.finditer(source):
            entity_type, index = match.group(1), int(match.group(2))
            if anon_map.token_counter.get(entity_type, 0) < index:
                anon_map.token_counter[entity_type] = index


def _mask_known_originals(
    text: str, anon_map: AnonymizationMap, known: set[str]
) -> str:
    """Replace known originals with their placeholders in one regex pass.

    Known placeholders are part of the alternation (tried first) and are
    emitted unchanged, so an original can never be substituted inside an
    existing placeholder.
    """
    originals = [o for o in anon_map.entity_map if o]
    if not originals:
        return text
    alternatives = sorted(known, key=len, reverse=True) + sorted(
        originals, key=len, reverse=True
    )
    pattern = re.compile("|".join(re.escape(a) for a in alternatives))
    return pattern.sub(
        lambda m: m.group(0) if m.group(0) in known else anon_map.entity_map[m.group(0)],
        text,
    )


def mask_known_entities(text: str, anon_map: AnonymizationMap) -> str:
    """Mask every original ``anon_map`` knows, then apply its token blocklist.

    Deterministic, no model inference. Existing placeholders are left intact
    even when a short original (for example a word also used as an entity
    type name) occurs inside them.
    """
    if not text:
        return text
    masked = _mask_known_originals(text, anon_map, _known_placeholders(anon_map))
    return anon_map.apply_blocklist(masked, language=anon_map.language)


def _protected_ranges(text: str, known: set[str]) -> List[Tuple[int, int]]:
    """Return ``(start, end)`` offsets of known placeholders in ``text``."""
    if not known:
        return []
    pattern = re.compile(
        "|".join(re.escape(p) for p in sorted(known, key=len, reverse=True))
    )
    return [m.span() for m in pattern.finditer(text)]


def _outside_ranges(
    start: int, end: int, ranges: Sequence[Tuple[int, int]]
) -> List[Tuple[int, int]]:
    """Split ``[start, end)`` into the sub-ranges not covered by ``ranges``."""
    pieces: List[Tuple[int, int]] = []
    cursor = start
    for r_start, r_end in ranges:
        if r_end <= cursor or r_start >= end:
            continue
        if r_start > cursor:
            pieces.append((cursor, r_start))
        cursor = max(cursor, r_end)
    if cursor < end:
        pieces.append((cursor, end))
    return pieces


def _trim(text: str, start: int, end: int) -> Optional[Tuple[int, int]]:
    """Trim whitespace/punctuation from a span; ``None`` if nothing remains."""
    while start < end and text[start] in _SPAN_EDGE_CHARS:
        start += 1
    while end > start and text[end - 1] in _SPAN_EDGE_CHARS:
        end -= 1
    if start >= end or not any(ch.isalnum() for ch in text[start:end]):
        return None
    return start, end


def _gate_one(
    text: str,
    anon_map: AnonymizationMap,
    sanitizer: "PIISanitizer",
    language: str,
) -> str:
    """Mask one untrusted text against ``anon_map``, registering new entities."""
    if not text:
        return text
    masked = _mask_known_originals(
        normalize_unicode(text), anon_map, _known_placeholders(anon_map)
    )
    masked = anon_map.apply_blocklist(masked, language=language)

    result = sanitizer.sanitize(
        masked, language, AnonymizationMap(document_id=0, language=language)
    )
    protected = _protected_ranges(masked, _known_placeholders(anon_map))
    pieces: List[Tuple[int, int, str]] = []
    for span in sorted(result.detected_spans, key=lambda s: s.start):
        for p_start, p_end in _outside_ranges(span.start, span.end, protected):
            trimmed = _trim(masked, p_start, p_end)
            if trimmed is not None:
                pieces.append((trimmed[0], trimmed[1], span.entity_type))
    if not pieces:
        return masked

    reserve_placeholder_counters(anon_map)
    mirror_lookup = bool(anon_map.placeholder_lookup)
    parts: List[str] = []
    cursor = 0
    for start, end, entity_type in pieces:
        if start < cursor:
            continue
        original = masked[start:end]
        placeholder = anon_map.add_entity(original, entity_type)
        if mirror_lookup:
            anon_map.placeholder_lookup.setdefault(placeholder, original)
        parts.append(masked[cursor:start])
        parts.append(placeholder)
        cursor = end
    parts.append(masked[cursor:])
    return anon_map.apply_blocklist("".join(parts), language=language)


async def gate_untrusted_texts(
    texts: Sequence[str],
    anon_map: AnonymizationMap,
    db: AsyncSession,
    settings: AppSettings,
    language: Optional[str] = None,
    sanitizer: Optional["PIISanitizer"] = None,
) -> List[str]:
    """Return ``texts`` with every known and newly detected PII value masked.

    ``anon_map`` is mutated: entities first seen here are added so the
    de-anonymizer can restore them. Callers must pass a request-local map
    (or a copy), never a cached per-document map object. A caller that
    holds a lock may pass a ``sanitizer`` built beforehand.
    """
    if not any(texts):
        return list(texts)
    if sanitizer is None:
        sanitizer = await create_sanitizer(db, settings, enable_ollama=False)
    lang = language or anon_map.language
    return await asyncio.to_thread(
        lambda: [_gate_one(t, anon_map, sanitizer, lang) for t in texts]
    )


async def gate_document_texts(
    db: AsyncSession,
    settings: AppSettings,
    document: Document,
    texts: Sequence[str],
) -> List[str]:
    """Gate texts that will be stored on ``document`` and persist its map.

    Works on a copy of the cached document map and replaces the cached map
    only after gating succeeds, so concurrent chat turns holding the old
    object never observe a half-updated map. The map is written (encrypted,
    all tiers) before the caller commits the text, so stored text never
    references a placeholder the map cannot resolve. Under the lock the
    map is read from the authoritative disk tier, so another worker's
    additions are never overwritten or their placeholder numbers reused.
    The sanitizer (model loading) is built before the lock is taken, and
    the lock is re-confirmed right before the write; a lost lock aborts
    with :class:`DocumentMapLockLost` and nothing is written.

    The delete endpoint erases the map and removes the row under the same
    lock, so the row is re-checked once the lock is held: a document
    deleted meanwhile aborts with HTTP 404 and its map is not recreated.
    """
    sanitizer = None
    if any(texts):
        sanitizer = await create_sanitizer(db, settings, enable_ollama=False)
    async with document_map_lock(document.id) as lock:
        still_exists = await db.scalar(
            select(Document.id).where(Document.id == document.id)
        )
        if still_exists is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document not found.",
            )
        cached = await load_persisted_document_map(document.id)
        if cached is not None:
            working = copy.deepcopy(cached)
        else:
            working = AnonymizationMap(
                document_id=document.id,
                language=document.language_override
                or document.detected_language
                or "en",
            )
            rows = await db.execute(
                select(Chunk.sanitized_text).where(Chunk.document_id == document.id)
            )
            reserve_placeholder_counters(working, rows.scalars().all())

        gated = await gate_untrusted_texts(
            texts, working, db, settings, sanitizer=sanitizer
        )

        changed = cached is None or (
            working.entity_map != cached.entity_map
            or working.token_to_placeholder != cached.token_to_placeholder
            or working.token_counter != cached.token_counter
        )
        if changed and working.entity_map:
            await lock.ensure_held()
            await set_document_map(document.id, working)
        return gated


__all__ = [
    "gate_document_texts",
    "gate_untrusted_texts",
    "mask_known_entities",
    "reserve_placeholder_counters",
]
