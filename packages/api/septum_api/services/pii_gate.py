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
from functools import cached_property
from typing import TYPE_CHECKING, Iterable, List, Optional, Sequence, Tuple

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import ContentFreeError
from ..models.document import Chunk, Document
from ..models.settings import AppSettings
from ..utils.db_helpers import document_language, effective_masking_language
from ..utils.text_utils import normalize_unicode
from .anonymization_map import PLACEHOLDER_PATTERN, AnonymizationMap
from .document_anon_store import (
    document_map_lock,
    load_persisted_document_map,
    set_document_map,
)
from .sanitizer_factory import create_sanitizer

if TYPE_CHECKING:
    from .sanitizer import PIISanitizer

_SPAN_EDGE_CHARS = " \t\r\n.,;:!?()[]{}\"'"


def _known_placeholders(anon_map: AnonymizationMap) -> set[str]:
    """Return every placeholder the map can resolve or has handed out."""
    known = set(anon_map.entity_map.values())
    known.update(anon_map.placeholder_lookup.keys())
    known.update(anon_map.token_to_placeholder.values())
    known.discard("")
    return known


def all_placeholders_known(text: str, anon_map: AnonymizationMap) -> bool:
    """Return whether every placeholder-shaped token in ``text`` resolves in ``anon_map``."""
    known = _known_placeholders(anon_map)
    return all(m.group(0) in known for m in PLACEHOLDER_PATTERN.finditer(text))


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
        for match in PLACEHOLDER_PATTERN.finditer(source):
            entity_type, index = match.group(1), int(match.group(2))
            if anon_map.token_counter.get(entity_type, 0) < index:
                anon_map.token_counter[entity_type] = index


def _map_snapshot(anon_map: AnonymizationMap) -> Tuple[dict, dict, dict]:
    """Copy the three maps that known placeholders and originals are read from."""
    return (
        dict(anon_map.entity_map),
        dict(anon_map.placeholder_lookup),
        dict(anon_map.token_to_placeholder),
    )


class _MaskState:
    """Masking state for one snapshot of an anonymization map.

    Building the originals alternation costs time proportional to the number
    of entities, so a gate call reuses one state across texts. The state
    keeps a copy of the map it was built from: :meth:`is_current` compares
    that copy with the live map, so a state is rebuilt whenever the map has
    changed, however it changed. Patterns compile on first use.
    """

    def __init__(self, anon_map: AnonymizationMap) -> None:
        self._entity_map, self._placeholder_lookup, self._token_to_placeholder = _map_snapshot(
            anon_map
        )
        self.known: set[str] = _known_placeholders(anon_map)

    def is_current(self, anon_map: AnonymizationMap) -> bool:
        """Return whether ``anon_map`` still holds the entries this state was built from."""
        return (
            self._entity_map == anon_map.entity_map
            and self._placeholder_lookup == anon_map.placeholder_lookup
            and self._token_to_placeholder == anon_map.token_to_placeholder
        )

    @cached_property
    def originals_pattern(self) -> Optional[re.Pattern[str]]:
        """Pattern matching known placeholders and originals, or ``None`` without originals."""
        return _originals_pattern(self._entity_map, self.known)

    @cached_property
    def protect_pattern(self) -> Optional[re.Pattern[str]]:
        """Pattern matching known placeholders, or ``None`` when there are none."""
        if not self.known:
            return None
        return re.compile("|".join(re.escape(p) for p in sorted(self.known, key=len, reverse=True)))

    def protected_ranges(self, text: str) -> List[Tuple[int, int]]:
        """Return ``(start, end)`` offsets of known placeholders in ``text``."""
        if self.protect_pattern is None:
            return []
        return [m.span() for m in self.protect_pattern.finditer(text)]

    def placeholder_for(self, match: re.Match[str]) -> str:
        """Return the replacement for one originals match: known placeholders stay as they are."""
        original = match.group(0)
        return original if original in self.known else self._entity_map[original]


def _originals_pattern(entity_map: dict, known: set[str]) -> Optional[re.Pattern[str]]:
    """Compile the alternation that matches known placeholders and originals.

    Known placeholders are tried first and are emitted unchanged, so an
    original can never be substituted inside an existing placeholder.
    Returns ``None`` when the map has no originals.
    """
    originals = [o for o in entity_map if o]
    if not originals:
        return None
    alternatives = [re.escape(a) for a in sorted(known, key=len, reverse=True)] + [
        _original_alternative(o) for o in sorted(originals, key=len, reverse=True)
    ]
    return re.compile("|".join(alternatives))


def _mask_known_originals(text: str, state: _MaskState) -> str:
    """Replace known originals with their placeholders in one regex pass.

    Originals without letters need digit boundaries (see
    :func:`_original_alternative`).
    """
    if state.originals_pattern is None:
        return text
    return state.originals_pattern.sub(state.placeholder_for, text)


def _original_alternative(original: str) -> str:
    """Return the regex alternative that matches ``original`` in text.

    An original without letters (a date, an amount, an id such as ``3/15``)
    must not extend a number: each end that is a digit may not touch another
    digit, so ``3/15`` is never found inside ``13/150`` while ``A4711`` and
    ``5(555) 1`` still mask. Originals with letters keep plain substring
    matching.
    """
    escaped = re.escape(original)
    if any(ch.isalpha() for ch in original):
        return escaped
    before = r"(?<!\d)" if original[0].isdigit() else ""
    after = r"(?!\d)" if original[-1].isdigit() else ""
    return f"{before}{escaped}{after}"


class KnownEntityMasker:
    """Mask known originals of one anonymization map across many texts.

    The masking state is built on the first text that needs it and reused for
    the rest. Each call checks it with :meth:`_MaskState.is_current`, so a map
    changed between calls is picked up without a stale mask.
    """

    def __init__(self, anon_map: AnonymizationMap) -> None:
        self._anon_map = anon_map
        self._state: Optional[_MaskState] = None

    def mask(self, text: str) -> str:
        """Mask every original the map knows, then apply its token blocklist.

        Deterministic, no model inference. Existing placeholders are left intact
        even when a short original (for example a word also used as an entity
        type name) occurs inside them.
        """
        if not text:
            return text
        if self._state is None or not self._state.is_current(self._anon_map):
            self._state = _MaskState(self._anon_map)
        masked = _mask_known_originals(text, self._state)
        return self._anon_map.apply_blocklist(masked, language=self._anon_map.language)


def mask_known_entities(text: str, anon_map: AnonymizationMap) -> str:
    """Mask one ``text`` against ``anon_map``; see :meth:`KnownEntityMasker.mask`."""
    return KnownEntityMasker(anon_map).mask(text)


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
    state: Optional[_MaskState] = None,
) -> str:
    """Mask one untrusted text against ``anon_map``, registering new entities.

    ``state`` must be current for ``anon_map`` (see :meth:`_MaskState.is_current`);
    when it is omitted a fresh one is built.
    """
    if not text:
        return text
    if state is None:
        state = _MaskState(anon_map)
    masked = _mask_known_originals(normalize_unicode(text), state)
    masked = anon_map.apply_blocklist(masked, language=language)

    result = sanitizer.sanitize(
        masked, language, AnonymizationMap(document_id=0, language=language)
    )
    protected = state.protected_ranges(masked)
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

    def gate_all() -> List[str]:
        state: Optional[_MaskState] = None
        gated: List[str] = []
        for text in texts:
            if text and (state is None or not state.is_current(anon_map)):
                state = _MaskState(anon_map)
            gated.append(_gate_one(text, anon_map, sanitizer, lang, state))
        return gated

    return await asyncio.to_thread(gate_all)


_MAP_MERGE_ATTEMPTS = 3


class DocumentMapMissingError(ContentFreeError, RuntimeError):
    """The document has no anonymization map (never written, or erased mid-turn)."""


class DocumentGoneError(HTTPException):
    """HTTP 404: the document row was deleted while its texts were being gated."""

    def __init__(self) -> None:
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")


class DocumentMapConflictError(HTTPException):
    """HTTP 409: the document map kept changing under concurrent writers."""

    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail="The document's anonymization map kept changing; try again.",
        )


def _same_map(a: Optional[AnonymizationMap], b: Optional[AnonymizationMap]) -> bool:
    """Return whether two persisted maps carry the same entities and counters."""
    if a is None or b is None:
        return a is b
    return (
        a.entity_map == b.entity_map
        and a.token_to_placeholder == b.token_to_placeholder
        and a.token_counter == b.token_counter
    )


async def _working_copy(
    db: AsyncSession,
    document: Document,
    base: Optional[AnonymizationMap],
    texts: Sequence[str],
) -> AnonymizationMap:
    """Return the map to gate against, with counters above every index in use.

    A document without a map gets a fresh one whose counters are reserved
    above the placeholders its stored chunks already carry. Indexes in
    ``texts`` are reserved in both cases, so a placeholder the map does not
    know is never handed to a different entity.
    """
    if base is not None:
        working = copy.deepcopy(base)
        reserve_placeholder_counters(working, texts)
        return working
    working = AnonymizationMap(
        document_id=document.id,
        language=document_language(document) or "en",
    )
    rows = await db.execute(
        select(Chunk.sanitized_text).where(Chunk.document_id == document.id)
    )
    reserve_placeholder_counters(working, [*rows.scalars().all(), *texts])
    return working


async def _require_row(db: AsyncSession, document: Document) -> None:
    """Raise HTTP 404 when the document row no longer exists."""
    still_exists = await db.scalar(select(Document.id).where(Document.id == document.id))
    if still_exists is None:
        raise DocumentGoneError()


async def _gate_and_merge(
    db: AsyncSession,
    settings: AppSettings,
    document: Document,
    texts: Sequence[str],
    sanitizer: Optional["PIISanitizer"],
    require_existing_map: bool,
) -> Tuple[List[str], bool]:
    """Gate ``texts`` outside the map lock, then merge and write under it.

    Each attempt reads the authoritative disk copy, gates a private copy of
    it (the sanitizer runs here, with no lock held), then takes the lock and
    compares the disk copy with the one it started from. Unchanged: the
    gated copy is written if it gained anything. Changed (another worker
    added entities meanwhile): the attempt is discarded and redone on the
    new copy, so no placeholder number is ever reused or overwritten. The
    row is checked before the sanitizer runs and again under the lock.
    Texts are masked in the document's effective masking language (see
    :func:`effective_masking_language`), not the language the map was built
    in, and the written map carries that language so every later reader
    (chat's unified map, :func:`mask_known_entities`) sees the same value.
    Returns the gated texts and whether the map was written.
    """
    if sanitizer is None and any(texts):
        sanitizer = await create_sanitizer(db, settings, enable_ollama=False)
    for _ in range(_MAP_MERGE_ATTEMPTS):
        await _require_row(db, document)
        base = await load_persisted_document_map(document.id)
        if base is None and require_existing_map:
            raise DocumentMapMissingError(f"Document {document.id} has no anonymization map")
        working = await _working_copy(db, document, base, texts)
        language = effective_masking_language(document)
        working.language = language
        gated = await gate_untrusted_texts(
            texts, working, db, settings, language=language, sanitizer=sanitizer
        )
        async with document_map_lock(document.id) as lock:
            await _require_row(db, document)
            current = await load_persisted_document_map(document.id)
            if current is None and require_existing_map:
                raise DocumentMapMissingError(
                    f"Anonymization map of document {document.id} was erased mid-turn"
                )
            if not _same_map(current, base):
                continue
            changed = (
                base is None
                or not _same_map(working, base)
                or base.language != working.language
            )
            written = bool(changed and working.entity_map)
            if written:
                await lock.ensure_held()
                await set_document_map(document.id, working)
            return gated, written
    raise DocumentMapConflictError()


async def gate_document_texts(
    db: AsyncSession,
    settings: AppSettings,
    document: Document,
    texts: Sequence[str],
    sanitizer: Optional["PIISanitizer"] = None,
) -> List[str]:
    """Gate texts against ``document``'s map and persist the entities they add.

    The cached map object is never mutated: gating works on a copy and the
    stored map is replaced only after it succeeds, so concurrent chat turns
    holding the old object never observe a half-updated map. The map is
    written (encrypted, all tiers) before the caller commits the text, so
    stored text never references a placeholder the map cannot resolve. The
    sanitizer runs before the map lock is taken (see :func:`_gate_and_merge`);
    the lock covers only the compare, merge and write, and is re-confirmed
    right before the write: a lost lock aborts with
    :class:`DocumentMapLockLost` and nothing is written.

    The delete endpoint erases the map and removes the row under the same
    lock, so the row is re-checked once the lock is held: a document
    deleted meanwhile aborts with HTTP 404 and its map is not recreated.
    A caller gating several documents may pass one ``sanitizer`` built
    beforehand.
    """
    gated, _ = await _gate_and_merge(
        db, settings, document, texts, sanitizer, require_existing_map=False
    )
    return gated


async def remask_document_texts(
    db: AsyncSession,
    settings: AppSettings,
    document: Document,
    texts: Sequence[str],
    sanitizer: "PIISanitizer",
) -> Tuple[List[str], bool]:
    """Re-mask stored chunk texts of ``document`` for a chat turn.

    Like :func:`gate_document_texts`, but the document must already have a
    map: one that is missing, or erased while the turn runs (a reprocess or
    delete), raises :class:`DocumentMapMissingError` instead of a partial map
    being rebuilt from these texts alone. Returns the texts and whether the
    map gained entities.
    """
    return await _gate_and_merge(
        db, settings, document, texts, sanitizer, require_existing_map=True
    )


__all__ = [
    "DocumentGoneError",
    "DocumentMapConflictError",
    "DocumentMapMissingError",
    "KnownEntityMasker",
    "all_placeholders_known",
    "gate_document_texts",
    "gate_untrusted_texts",
    "mask_known_entities",
    "remask_document_texts",
    "reserve_placeholder_counters",
]
