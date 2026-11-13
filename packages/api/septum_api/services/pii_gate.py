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
import logging
import re
from bisect import bisect_left
from functools import cached_property
from typing import TYPE_CHECKING, Callable, Iterable, List, Optional, Sequence, Tuple

from fastapi import HTTPException, status
from septum_core.detector import _MAX_SEGMENTS_PER_DOCUMENT
from septum_core.field_label_cues import field_label_cue_pattern
from septum_core.language_resolution import iso_language_code
from septum_core.language_segments import MAX_DETECTOR_CALLS_PER_DOCUMENT, Segment
from septum_core.spans import entity_range
from septum_core.text_boundaries import CAMEL_STEP, TOKEN_END, TOKEN_START
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import ContentFreeError
from ..models.document import Chunk, Document
from ..models.settings import AppSettings
from ..utils.db_helpers import (
    document_language,
    effective_masking_language,
    effective_masking_source,
    masking_segments,
)
from ..utils.text_utils import normalize_for_comparison, normalize_unicode
from .anonymization_map import PLACEHOLDER_PATTERN, AnonymizationMap
from .document_anon_store import (
    document_map_lock,
    load_persisted_document_map,
    set_document_map,
)
from .policy_composer import ComposedPolicy
from .sanitizer_factory import create_sanitizer

if TYPE_CHECKING:
    from .sanitizer import PIISanitizer

logger = logging.getLogger(__name__)

_SPAN_EDGE_CHARS = " \t\r\n.,;:!?()[]{}\"'"
_WORD_CHAR = re.compile(r"\w")


def is_letter_fragment(value: str) -> bool:
    """Return whether ``value`` is a bare letter, too short to search for as free text.

    A value made of one cased letter, optionally followed by a dot ("T",
    "T."), may be a real entity where a recognizer found it (a sex marker
    "K"), and is masked and registered there. Searched for in other text,
    it would mask that letter in every word, so :func:`_originals_pattern`
    leaves it out (the blocklist skips tokens this short too); it is found
    only where it stands alone (see :func:`_standalone_fragments_pattern`).
    A character of an uncased script is a whole syllable or word on its own
    (a one-syllable surname "김", a glyph of a script written without
    spaces) and never a fragment.
    """
    stem = value[:-1] if value.endswith(".") else value
    return len(stem) == 1 and stem.isalpha() and stem.lower() != stem.upper()


def _is_symbol_fragment(value: str) -> bool:
    """Whether ``value`` is one letter plus symbols ("A+", "O-"), searched only as a whole token."""
    return (
        not is_letter_fragment(value)
        and any(ch.isalpha() and ch.lower() != ch.upper() for ch in value)
        and sum(1 for ch in value if ch.isalnum()) < 2
    )


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
    def fragments_pattern(self) -> Optional[re.Pattern[str]]:
        """Pattern for bare-letter originals standing alone, or ``None`` without any."""
        return _standalone_fragments_pattern(self._entity_map, self.known)

    @cached_property
    def protect_pattern(self) -> Optional[re.Pattern[str]]:
        """Pattern matching known placeholders, or ``None`` when there are none."""
        if not self.known:
            return None
        return re.compile(_guarded([re.escape(p) for p in _longest_first(self.known)], self.known))

    def protected_ranges(self, text: str) -> List[Tuple[int, int]]:
        """Return ``(start, end)`` offsets of known placeholders in ``text``."""
        if self.protect_pattern is None:
            return []
        return [m.span() for m in self.protect_pattern.finditer(text)]

    def placeholder_for(self, match: re.Match[str]) -> str:
        """Return the replacement for one originals match: known placeholders stay as they are.

        A match of the field-label alternative replaces the value glued to
        the label (see :func:`_originals_pattern`) and keeps the label, unless
        the label is itself an original ("ADI" as a name in "ADIKAYA").
        """
        glued = match.groupdict().get(_GLUED)
        if glued is not None:
            cue = match.group(_CUE)
            return self._entity_map.get(cue, cue) + self._entity_map[glued]
        original = match.group(0)
        return original if original in self.known else self._entity_map[original]


_CUE = "cue"
_GLUED = "glued"
_FIELD_LABEL_CUE = field_label_cue_pattern()


def _longest_first(values: Iterable[str]) -> List[str]:
    """``values`` sorted longest first, so an alternation prefers the longest match."""
    return sorted(values, key=len, reverse=True)


def _guarded(alternatives: Sequence[str], values: Iterable[str], edge: str = "") -> str:
    """Alternation of ``alternatives`` behind a lookahead on the first characters of ``values``.

    The regex engine tries every alternative at every position; the
    lookahead fails at once where no value can start, so a group costs one
    character test there. ``edge`` is a zero-width check placed after the
    lookahead. Returns ``""`` for no alternatives.
    """
    if not alternatives:
        return ""
    first = "".join(sorted({re.escape(v[0]) for v in values}))
    return f"(?=[{first}]){edge}(?:{'|'.join(alternatives)})"


def _originals_pattern(entity_map: dict, known: set[str]) -> Optional[re.Pattern[str]]:
    """Compile the alternation that matches known placeholders and originals.

    Known placeholders are tried first and are emitted unchanged, so an
    original can never be substituted inside an existing placeholder.
    Letter fragments (see :func:`is_letter_fragment`) stay in the map for
    de-anonymization but are never searched for. Returns ``None`` when the
    map has no originals left to search for.

    Edges: an original with a digit keeps digit boundaries only
    (:func:`_original_alternative`). A letter original is open on the
    right, so a suffix written onto a name ("Yılmazın") does not hide it;
    masking "Ann" inside "Annäherung" is the accepted cost. Most letter
    originals are open on the left too (:func:`_opens_anywhere`: title case,
    several words or a long value), so they mask when glued to the word
    before them ("ÜNVANIACME LOJİSTİK", "ProjeTitan", "4711AHMET ÇELİK").
    A short single token in one case ("KVKK", "ST.") needs a left edge:
    :data:`TOKEN_START` (Unicode-aware, so a name written against an
    unspaced glyph still masks), a lowercase-to-capital step ("imzaKAYA"),
    the end of a field label ("SOYADIKAYA", see
    :mod:`septum_core.field_label_cues`), or the end of its own word
    ("SOYADKAYA", "4471KAYA"). A short token inside a word ("XKAYALAR", "T"
    in "AYDINLATMA") stays as it is, by design: glued on both sides with no
    label at a word start before it ("SOYADKAYA1990",
    "MÜŞTERİSOYADIKAYAFATURA") it is not found, since a match there would
    also hit every word that merely contains it. One that ends a longer
    word is masked there ("VALİ" -> "V[…]" when "ALİ" is an original), the
    accepted cost of not leaking a value glued to any label. A symbol
    fragment ("A+") matches only as a whole token (:func:`_whole_token`).

    The groups, in order, each longest first: originals with a digit,
    letter originals open on the left, short tokens glued to a field label
    (tried before the plain short tokens, so "ALICIALI" is not read as
    "ALI"+"CIALI"), short tokens at a word start or a case step, short
    tokens that end their word, symbol fragments, and originals that open
    with a non-word character. "Longest original wins" at every position
    still holds: two originals only both match where one is a prefix of the
    other, a prefix never gains a digit, and an original that extends an
    open one is open too. Each group sits behind a lookahead on its first
    characters (:func:`_guarded`).
    """
    originals = [o for o in entity_map if o and not is_letter_fragment(o)]
    if not originals:
        return None
    by_length = _longest_first(originals)
    with_digit = [o for o in by_length if _has_digit(o)]
    letters_only = [o for o in by_length if not _has_digit(o)]
    symbols = [o for o in letters_only if _is_symbol_fragment(o)]
    words = [o for o in letters_only if not _is_symbol_fragment(o)]
    open_left = [o for o in words if _WORD_CHAR.match(o[0]) and _opens_anywhere(o)]
    edged = [o for o in words if _WORD_CHAR.match(o[0]) and not _opens_anywhere(o)]
    unedged = [o for o in words if not _WORD_CHAR.match(o[0])]
    edged_alternatives = [re.escape(o) for o in edged]
    edged_alternation = "|".join(edged_alternatives)
    groups = [
        _guarded([re.escape(k) for k in _longest_first(known)], known),
        _guarded([_original_alternative(o) for o in with_digit], with_digit),
        _guarded([re.escape(o) for o in open_left], open_left),
        (
            f"{TOKEN_START}(?P<{_CUE}>(?i:{_FIELD_LABEL_CUE}))(?P<{_GLUED}>{edged_alternation})"
            if edged
            else ""
        ),
        _guarded(edged_alternatives, edged, edge=f"(?:{TOKEN_START}|{CAMEL_STEP})"),
        _word_end_group(edged),
        "|".join(_whole_token(o) for o in symbols),
        _guarded([re.escape(o) for o in unedged], unedged),
    ]
    return re.compile("|".join(g for g in groups if g))


_SHORT_TOKEN_LENGTH = 5
_WORD_RUN = re.compile(r"\w+")


def _word_end_group(edged: Sequence[str]) -> str:
    """Alternative for short tokens that end their word, wherever the word starts.

    Only tokens made of word characters can end a word this way. After the
    first-character check, a lookahead checks that a word ends within the
    longest such token from here, so the alternation is tried only near word
    ends; short tokens are under ``_SHORT_TOKEN_LENGTH`` characters, which
    bounds that reach.
    """
    words = [o for o in edged if _WORD_RUN.fullmatch(o)]
    if not words:
        return ""
    reach = max(len(o) for o in words)
    near_end = rf"(?=\w{{1,{reach}}}{TOKEN_END})"
    return f"{_guarded([re.escape(o) for o in words], words, edge=near_end)}(?={TOKEN_END})"


def _opens_anywhere(value: str) -> bool:
    """Whether a letters-only original may match glued to the preceding word.

    A title-case original ("Acme", "Ann") marks its own start with a
    capital, and a longer one (several words, or at least
    ``_SHORT_TOKEN_LENGTH`` characters, in any case: "ACME LOJİSTİK",
    "ahmet çelik") is distinctive enough to be found inside a run
    ("ÜNVANIACME LOJİSTİK", "adsoyadahmet çelik"). Only a short single
    token in one case ("KVKK", "ST.") needs a word start, a field label or
    the end of its word, since it would otherwise turn up inside ordinary
    words. Length is counted in characters, so a padded value
    ("A___…___B") is never short.
    """
    if _is_title_case(value):
        return True
    return any(ch.isspace() for ch in value) or len(value) >= _SHORT_TOKEN_LENGTH


def _whole_token(value: str) -> str:
    """Regex for ``value`` standing as a whole token: no word character on either side."""
    before = TOKEN_START if _WORD_CHAR.match(value[0]) else r"(?<!\w)"
    return rf"{before}{re.escape(value)}(?!\w)"


def _is_title_case(value: str) -> bool:
    """Whether ``value`` opens with a capital and holds a lowercase letter ("Acme Lojistik")."""
    return value[0].isupper() and any(ch.islower() for ch in value)


def _has_digit(value: str) -> bool:
    """Return whether ``value`` holds a digit of any script."""
    return any(ch.isdigit() for ch in value)


def _mask_known_originals(text: str, state: _MaskState) -> str:
    """Replace known originals with their placeholders in one regex pass.

    Edges an original must respect are described in :func:`_originals_pattern`.
    """
    if state.originals_pattern is None:
        return text
    return state.originals_pattern.sub(state.placeholder_for, text)


def _original_alternative(original: str) -> str:
    """Return the regex alternative that matches an original holding a digit.

    Such an original (a date, an amount, an id such as ``3/15`` or an IBAN)
    must not extend a number: each end that is a digit may not touch another
    digit, so ``3/15`` is never found inside ``13/150`` while ``A4711``,
    ``XA4711`` and an IBAN glued to its label ("IBANTR33…") still mask.
    Letter originals are grouped in :func:`_originals_pattern`.
    """
    escaped = re.escape(original)
    before = r"(?<!\d)" if original[0].isdigit() else ""
    after = r"(?!\d)" if original[-1].isdigit() else ""
    return f"{before}{escaped}{after}"


def mask_known_entities(text: str, anon_map: AnonymizationMap) -> str:
    """Mask every original ``anon_map`` knows, then apply its token blocklist.

    Deterministic, no model inference. Existing placeholders are left intact
    even when a short original (for example a word also used as an entity
    type name) occurs inside them.
    """
    if not text:
        return text
    return _mask_with_state(text, _MaskState(anon_map), anon_map)


def _mask_originals(text: str, state: _MaskState) -> str:
    """Mask the known originals, then the bare-letter originals standing alone.

    Originals go first, so a longer value holding a bare letter ("A. Yılmaz")
    is masked whole before the letter alone could be.
    """
    masked = _mask_known_originals(text, state)
    if state.fragments_pattern is not None:
        masked = state.fragments_pattern.sub(state.placeholder_for, masked)
    return masked


def _mask_with_state(
    text: str, state: _MaskState, anon_map: AnonymizationMap, language: Optional[str] = None
) -> str:
    """Mask the originals of ``state`` in ``text``, then apply ``anon_map``'s blocklist in ``language`` (the map's by default)."""
    return anon_map.apply_blocklist(_mask_originals(text, state), language=language)


def _heading_line(title: str, source_text: str) -> Optional[str]:
    """Return the whole line of ``source_text`` that ``title`` opens, or ``None``."""
    for line in source_text.splitlines():
        stripped = line.strip()
        if stripped.startswith(title):
            return stripped
    return None


def _cut_outside_placeholders(text: str, limit: int) -> str:
    """Cut ``text`` to ``limit`` characters, moving the cut past a placeholder it would split."""
    if len(text) <= limit:
        return text
    for match in PLACEHOLDER_PATTERN.finditer(text):
        if match.start() < limit < match.end():
            return text[: match.end()]
    return text[:limit]


_WHITESPACE = re.compile(r"\s+")
_PRUNE_CHARS = 4


def _without_spaces(value: str) -> str:
    """``value`` with every whitespace character removed."""
    return _WHITESPACE.sub("", value)


# A token edge at a position, as the body pass reads it: a word start or a
# letter of a script whose words touch ("联系人张三").
_TOKEN_STARTS_HERE = re.compile(TOKEN_START)
# A field label at a word start, ending where a search window ends.
_LABEL_ENDS_HERE = re.compile(rf"{TOKEN_START}(?i:(?:{_FIELD_LABEL_CUE}))\Z")


def _start_of_last(text: str, count: int) -> int:
    """Offset where the last ``count`` non-space characters of ``text`` begin.

    A tail is compared without whitespace, so the window for it is measured
    in non-space characters: a value written with extra spaces
    ("TR94  0006  1005…") is still inside it.
    """
    seen = 0
    for index in range(len(text) - 1, -1, -1):
        if not text[index].isspace():
            seen += 1
            if seen == count:
                return index
    return 0


class _CutOffIndex:
    """Known originals arranged for :meth:`find`, built once per title masker.

    Each original is keyed by its comparison form
    (:func:`normalize_for_comparison` in the map language). An original with
    a digit is keyed without whitespace, so a value written with other
    spacing ("TR94 0006" against "TR940006…") still meets it.
    """

    def __init__(self, originals: Iterable[str], language: str) -> None:
        self._language = language
        entries = []
        for original in originals:
            key = normalize_for_comparison(original, language)
            if _has_digit(original):
                key = _without_spaces(key)
            entries.append((key, original))
        entries.sort()
        self._entries = entries
        self._keys = [key for key, _ in entries]
        self._longest = max((len(_without_spaces(o)) for _, o in entries), default=0)
        self._prefixes = {
            _without_spaces(key)[:_PRUNE_CHARS]
            for key in self._keys
            if len(_without_spaces(key)) >= _PRUNE_CHARS
        }

    def _opened(self, probe: str, any_original: bool) -> Optional[str]:
        """The first original longer than ``probe`` whose key starts with it.

        Without ``any_original`` (a tail glued to the word before it) only
        originals that may match glued are taken (:func:`_opens_anywhere`).
        """
        index = bisect_left(self._keys, probe)
        while index < len(self._keys) and self._keys[index].startswith(probe):
            key, original = self._entries[index]
            if len(key) > len(probe) and (any_original or _opens_anywhere(original)):
                return original
            index += 1
        return None

    def find(self, text: str) -> Optional[Tuple[int, str]]:
        """Find where ``text`` ends in the cut-off beginning of a known original.

        Returns ``(start, original)`` for the longest tail of ``text`` that
        holds no placeholder, has at least two letters or digits (a shorter
        tail opens too many words) and opens a longer original; case does
        not matter ("KİRACI AHMET ÇEL" opens "Ahmet Çelik"). A tail starts
        at a token, or glued to the preceding word when the original it
        opens may match there (:func:`_opens_anywhere`: "KİRACIAhmet Çel"),
        or right after a digit, a token edge of a script whose words touch,
        or a field label at a word start, where any original may
        ("No4471KA" and "SOYADIKAY" open "KAYA", "联系人张三" opens "张三丰"),
        as the body pass masks a short value there.
        Only tails after the last placeholder and with no more non-space
        characters than the longest original are tried; a tail whose first
        characters open no original is skipped before it is folded, and the
        rest are looked up by
        bisection, so the cost does not grow with ``text``.
        """
        if not self._entries:
            return None
        floor = _start_of_last(text, self._longest)
        for match in PLACEHOLDER_PATTERN.finditer(text):
            floor = max(floor, match.end())
        for start in range(floor, len(text)):
            if not text[start].isalnum():
                continue
            lead = _without_spaces(
                normalize_for_comparison(text[start : start + 4 * _PRUNE_CHARS], self._language)
            )[:_PRUNE_CHARS]
            if len(lead) == _PRUNE_CHARS and lead not in self._prefixes:
                continue
            tail = text[start:]
            if "[" in tail or sum(1 for ch in tail if ch.isalnum()) < 2:
                continue
            folded = normalize_for_comparison(tail, self._language)
            any_original = (
                _TOKEN_STARTS_HERE.match(text, start) is not None
                or not text[start - 1].isalpha()
                or _LABEL_ENDS_HERE.search(text, 0, start) is not None
            )
            for probe in dict.fromkeys((folded, _without_spaces(folded))):
                opened = self._opened(probe, any_original)
                if opened is not None:
                    return start, opened
        return None


def section_title_masker(
    anon_map: Optional[AnonymizationMap],
) -> Callable[[Optional[str], str], Optional[str]]:
    """Return a function that masks section titles against ``anon_map``.

    The returned ``mask(title, source_text)`` reuses one compiled masking
    state, so masking many titles of one document compiles the map once.

    A title is the opening of a heading line, cut to a fixed length, so it
    can end inside a value ("IBAN: TR94 0006"). When ``source_text`` (the
    raw text the title was taken from) holds the whole heading line, that
    line is masked first and only then cut back to the title's length, so
    a value the cut would have split is masked whole. Otherwise the title is
    masked on its own, except a tail that opens a known original: that tail
    is found on the title as it was (see :meth:`_CutOffIndex.find`), before
    any masking could split it, and replaced by the original's placeholder.
    A bare-letter value of the map standing alone ("Cinsiyet: K") is masked
    as in any text (see :func:`_standalone_fragments_pattern`). Without a
    map nothing can be masked, so every title comes back as ``None``: a
    stored title is never passed on raw. An empty or missing title comes
    back as it is.
    """
    if anon_map is None:

        def dropped(title: Optional[str], source_text: str = "") -> Optional[str]:
            return title if not title else None

        return dropped

    state = _MaskState(anon_map)
    cut_offs: Optional[_CutOffIndex] = None

    def masked_title(title: str, source_text: str) -> str:
        nonlocal cut_offs
        line = _heading_line(title, normalize_unicode(source_text or ""))
        if line is None:
            if cut_offs is None:
                cut_offs = _CutOffIndex(
                    (o for o in anon_map.entity_map if o and not is_letter_fragment(o)),
                    anon_map.language,
                )
            cut = cut_offs.find(title)
            if cut is None:
                return _mask_with_state(title, state, anon_map)
            start, opened = cut
            return _mask_with_state(title[:start], state, anon_map) + anon_map.entity_map[opened]
        masked = _mask_with_state(line, state, anon_map)
        if line == title:
            return masked
        return _cut_outside_placeholders(masked, len(title))

    def mask(title: Optional[str], source_text: str = "") -> Optional[str]:
        if not title:
            return title
        return masked_title(normalize_unicode(title), source_text)

    return mask


def _standalone_fragments_pattern(entity_map: dict, known: set[str]) -> Optional[re.Pattern[str]]:
    """Pattern for bare-letter originals standing alone in any masked text.

    A value such as "Cinsiyet: K" puts a bare-letter original
    (:func:`is_letter_fragment`) on its own, where it is the value, not part
    of a word. Such a fragment matches only with no word character or dot
    before it and no word character (or dot plus word character, as in
    "T.C.") after it, so "T" stays in "T.C." and in "AYDINLATMA". Known
    placeholders are matched first and kept, so no fragment is found inside
    one. Symbol fragments ("A+") need nothing here: the main pass already
    masks them as whole tokens, a looser edge than this one.
    """
    values = _longest_first(o for o in entity_map if o and is_letter_fragment(o))
    if not values:
        return None
    alone = rf"(?<![\w.])(?:{'|'.join(re.escape(v) for v in values)})(?!\.?\w)"
    protected = _guarded([re.escape(p) for p in _longest_first(known)], known)
    return re.compile(f"{protected}|{alone}" if protected else alone)


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
    segmenter: Optional["TextSegmenter"] = None,
) -> str:
    """Mask one untrusted text against ``anon_map``, registering new entities.

    ``state`` must be current for ``anon_map`` (see :meth:`_MaskState.is_current`);
    when it is omitted a fresh one is built. With a ``segmenter`` the text
    is detected per language segment (see :class:`TextSegmenter`).
    """
    if not text:
        return text
    if state is None:
        state = _MaskState(anon_map)
    masked = _premask(text, anon_map, state, language)
    segments = None if segmenter is None else segmenter.segments(text, masked, state, language)
    pieces = _detected_pieces(masked, sanitizer, language, state, segments)
    if not pieces:
        return masked
    reserve_placeholder_counters(anon_map)
    return _replace_pieces(masked, pieces, anon_map, language)


_Piece = Tuple[int, int, str, Optional[Tuple[int, int]]]
"""A detected range to replace: ``(start, end, entity_type, core)``."""

LABEL_SEPARATOR = "\n\n"
"""Joins the labels :func:`_gate_joined` gates as one text.

A paragraph break, so detectors treat each label as its own sentence.
Labels are mapped back by offset, never by splitting on it, so a label
that holds the separator itself is still mapped correctly.
"""


def _premask(
    text: str, anon_map: AnonymizationMap, state: _MaskState, language: str
) -> str:
    """:func:`_mask_with_state` on ``text`` normalized to NFC."""
    return _mask_with_state(normalize_unicode(text), state, anon_map, language)


_LINE_BREAKS = re.compile(r"\n+")


def _language_view(masked: str, state: _MaskState) -> str:
    """``masked`` with placeholder tokens blanked to spaces, offsets unchanged.

    A placeholder's Latin capitals would otherwise count as text of a
    Latin-script language when the text's languages are judged.
    """
    chars = list(PLACEHOLDER_PATTERN.sub(lambda match: " " * len(match.group()), masked))
    for start, end in state.protected_ranges(masked):
        chars[start:end] = " " * (end - start)
    return "".join(chars)


class TextSegmenter:
    """Language segments for the texts of one chat turn, under one budget they share.

    Only texts masked in a detected language are given to it: a language
    the user chose is never segmented (see
    :func:`~septum_api.utils.db_helpers.effective_masking_source`), and the
    caller leaves those texts out.

    Ingestion bounds a document's segmentation by core's per-document caps
    (:data:`~septum_core.language_segments.MAX_DETECTOR_CALLS_PER_DOCUMENT`
    language detector calls, the detector's ``_MAX_SEGMENTS_PER_DOCUMENT``
    segments). One segmenter applies the same caps to every text of every
    document it serves, so a turn costs no more than one document would.
    Once either is spent, every later text is detected in its dominant
    language alone, exactly as without segmentation, and one content-free
    warning says so. Such a text is recorded in :attr:`degraded` (by the
    text the caller passed, before premasking), so a caller that caches the
    result can leave it out. Results are remembered per premasked text and
    language, so a retried map merge neither judges a text again nor spends
    more budget. Not thread-safe: one gate call uses it from one thread.
    """

    def __init__(self) -> None:
        self.calls_left = MAX_DETECTOR_CALLS_PER_DOCUMENT
        self.segments_left = _MAX_SEGMENTS_PER_DOCUMENT
        self.degraded: set[str] = set()
        self._texts = 0
        self._known: dict[Tuple[str, str], Tuple[Optional[Tuple[Segment, ...]], bool]] = {}

    @property
    def spent(self) -> bool:
        """Whether the budget is used up, so no further text is segmented."""
        return self.calls_left <= 0 or self.segments_left <= 0

    def segments(
        self, text: str, masked: str, state: _MaskState, language: str
    ) -> Optional[Tuple[Segment, ...]]:
        """Segments of ``masked`` (premasked from ``text``), or ``None`` to detect it in ``language`` alone.

        A stored chunk keeps no block offsets, so it is cut after each run
        of line breaks: blocks are joined by a newline or a blank line, and
        short lines are coalesced before any is judged.
        :func:`masking_segments` judges them on :func:`_language_view`;
        segments all in ``language`` (a text in one language) are ``None``,
        so such a text takes exactly the single-language path. A text the
        budget did not cover (spent before it, used up part-way through it,
        or its segments did not fit) is ``None`` and joins :attr:`degraded`.
        CPU-bound: call it off the event loop. Errors propagate.
        """
        key = (masked, language)
        if key not in self._known:
            self._known[key] = self._judge(masked, state, language)
        segments, degraded = self._known[key]
        if degraded:
            self.degraded.add(text)
        return segments

    def _judge(
        self, masked: str, state: _MaskState, language: str
    ) -> Tuple[Optional[Tuple[Segment, ...]], bool]:
        """``(segments, degraded)`` of a text not judged before, spending the budget."""
        if self.spent:
            return None, True
        self._texts += 1
        segments = masking_segments(
            _language_view(masked, state),
            [0, *(match.end() for match in _LINE_BREAKS.finditer(masked))],
            language,
            "detected",
            max_detector_calls=self.calls_left,
            on_detector_call=self._count_call,
        )
        degraded = False
        if segments is None or {s.language for s in segments} <= {iso_language_code(language)}:
            # Core answers a text it ran out of calls on with its dominant language.
            degraded = self.calls_left <= 0
            segments = None
        elif len(segments) > self.segments_left:
            self.segments_left = 0
            segments, degraded = None, True
        else:
            self.segments_left -= len(segments)
        if self.spent:
            logger.warning(
                "Language segmentation budget spent on text %d of the turn; later texts are masked in their dominant language",
                self._texts,
            )
        return segments, degraded

    def _count_call(self) -> None:
        self.calls_left -= 1


def _detected_pieces(
    masked: str,
    sanitizer: "PIISanitizer",
    language: str,
    state: _MaskState,
    segments: Optional[Sequence[Segment]] = None,
) -> List[_Piece]:
    """Run the sanitizer over ``masked`` and return what it finds outside known placeholders, trimmed.

    ``segments`` (offsets into ``masked``) go to the sanitizer's detector
    with ``language`` as the dominant language; without them the call is
    the single-language :meth:`~septum_core.detector.Detector.sanitize`.
    """
    scratch = AnonymizationMap(document_id=0, language=language)
    if segments is None:
        result = sanitizer.sanitize(masked, language, scratch)
    else:
        result = sanitizer.apply(sanitizer.detect(masked, language, segments=segments), scratch)
    protected = state.protected_ranges(masked)
    pieces: List[_Piece] = []
    for span in sorted(result.detected_spans, key=lambda s: s.start):
        for p_start, p_end in _outside_ranges(span.start, span.end, protected):
            trimmed = _trim(masked, p_start, p_end)
            if trimmed is not None:
                pieces.append((trimmed[0], trimmed[1], span.entity_type, span.core))
    return pieces


def _replace_pieces(
    masked: str, pieces: Sequence[_Piece], anon_map: AnonymizationMap, language: str
) -> str:
    """Replace each piece of ``masked`` with the placeholder of its entity, registering new ones.

    Counters must already be reserved (:func:`reserve_placeholder_counters`).
    """
    mirror_lookup = bool(anon_map.placeholder_lookup)
    parts: List[str] = []
    cursor = 0
    for start, end, entity_type, core in pieces:
        if start < cursor:
            continue
        core_start, core_end = entity_range(start, end, core, masked)
        original = masked[core_start:core_end]
        placeholder = anon_map.add_entity(original, entity_type)
        if mirror_lookup:
            anon_map.placeholder_lookup.setdefault(placeholder, original)
        parts.append(masked[cursor:start])
        parts.append(placeholder)
        cursor = end
    parts.append(masked[cursor:])
    return anon_map.apply_blocklist("".join(parts), language=language)


def _pieces_within(
    pieces: Sequence[_Piece], low: int, high: int, joined: str
) -> List[_Piece]:
    """The parts of ``pieces`` inside ``[low, high)`` of ``joined``, trimmed and shifted to start at 0.

    A piece that crosses a label's edge is cut at it, so each label gets
    only the characters of the piece that lie in that label, at their own
    offsets.
    """
    own: List[_Piece] = []
    for start, end, entity_type, core in pieces:
        if start >= high or end <= low:
            continue
        trimmed = _trim(joined, max(start, low), min(end, high))
        if trimmed is None:
            continue
        shifted_core = None if core is None else (core[0] - low, core[1] - low)
        own.append((trimmed[0] - low, trimmed[1] - low, entity_type, shifted_core))
    return own


def _gate_joined(
    texts: Sequence[str],
    anon_map: AnonymizationMap,
    sanitizer: "PIISanitizer",
    language: str,
) -> List[str]:
    """Gate ``texts`` with one sanitizer run over all of them, joined by :data:`LABEL_SEPARATOR`.

    Built for short texts such as spreadsheet column labels, where a run
    per text would cost one set of local-model calls each. Every text is
    first masked against the known originals, then the joined text is
    sanitized once and each detected range is mapped back by offset to the
    texts it covers (:func:`_pieces_within`). Last, every text is masked
    again against the map, so a value found in one text is masked in every
    other text it occurs in, even where the detector reported only its
    first occurrence.
    """
    state = _MaskState(anon_map)
    premasked = [_premask(text, anon_map, state, language) for text in texts]
    bounds: List[Tuple[int, int]] = []
    cursor = 0
    for masked in premasked:
        bounds.append((cursor, cursor + len(masked)))
        cursor += len(masked) + len(LABEL_SEPARATOR)
    joined = LABEL_SEPARATOR.join(premasked)
    pieces = _detected_pieces(joined, sanitizer, language, state)
    if not pieces:
        return premasked
    reserve_placeholder_counters(anon_map)
    gated = []
    for (low, high), masked in zip(bounds, premasked):
        own = _pieces_within(pieces, low, high, joined)
        gated.append(_replace_pieces(masked, own, anon_map, language) if own else masked)
    final_state = _MaskState(anon_map)
    return [_mask_with_state(text, final_state, anon_map, language) for text in gated]


async def create_user_text_sanitizer(
    db: AsyncSession,
    settings: AppSettings,
    enable_ollama: bool = False,
    policy: Optional[ComposedPolicy] = None,
) -> "PIISanitizer":
    """Build the sanitizer for text a user typed or may have edited.

    Custom rules mask every match here, without their context-word gate: a
    short question or label seldom carries the context a document would, and
    an edited chunk may have lost it. Missing a value costs more than
    masking one too many. Chat queries, everything gated by this module and
    the chat re-mask of outdated chunks (a chunk alone lacks the text around
    it) use it.
    """
    return await create_sanitizer(
        db, settings, enable_ollama=enable_ollama, policy=policy, context_gate=False
    )


def _require_ungated(sanitizer: Optional["PIISanitizer"]) -> None:
    """Refuse a sanitizer that keeps custom rules' context-word gate.

    Gated custom rules would let a value typed without its context words
    through to the cloud LLM. A sanitizer that does not expose
    ``context_gate`` is taken as gated; ``None`` (build one here) is allowed.
    """
    if sanitizer is not None and getattr(sanitizer, "context_gate", True):
        raise ValueError("User text needs a sanitizer from create_user_text_sanitizer")


async def gate_untrusted_texts(
    texts: Sequence[str],
    anon_map: AnonymizationMap,
    db: AsyncSession,
    settings: AppSettings,
    language: Optional[str] = None,
    sanitizer: Optional["PIISanitizer"] = None,
    joined: bool = False,
    segmenter: Optional[TextSegmenter] = None,
) -> List[str]:
    """Return ``texts`` with every known and newly detected PII value masked.

    ``anon_map`` is mutated: entities first seen here are added so the
    de-anonymizer can restore them. Callers must pass a request-local map
    (or a copy), never a cached per-document map object. A caller that
    holds a lock may pass a ``sanitizer`` built beforehand by
    :func:`create_user_text_sanitizer`; a context-gated one raises
    :class:`ValueError`. With ``joined`` the texts are sanitized in one run
    (:func:`_gate_joined`), for many short texts such as column labels.
    With a ``segmenter`` each text is detected per language segment,
    ``language`` staying the dominant one; it needs an explicit
    ``language`` and cannot be combined with ``joined`` (both raise
    :class:`ValueError`).
    """
    _require_ungated(sanitizer)
    if segmenter is not None and (joined or language is None):
        raise ValueError("Segmented gating needs a language and separate texts")
    if not any(texts):
        return list(texts)
    if sanitizer is None:
        sanitizer = await create_user_text_sanitizer(db, settings)
    lang = language or anon_map.language

    def gate_all() -> List[str]:
        if joined:
            return _gate_joined(texts, anon_map, sanitizer, lang)
        state: Optional[_MaskState] = None
        gated: List[str] = []
        for text in texts:
            if text and (state is None or not state.is_current(anon_map)):
                state = _MaskState(anon_map)
            gated.append(_gate_one(text, anon_map, sanitizer, lang, state, segmenter))
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
    joined: bool = False,
    segmenter: Optional[TextSegmenter] = None,
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
    A ``segmenter`` detects each text per language segment, with its one
    budget for every text and every attempt, unless the language is the
    user's override.
    Returns the gated texts and whether the map was written. A context-gated
    ``sanitizer`` raises :class:`ValueError` before anything is read.
    """
    _require_ungated(sanitizer)
    if sanitizer is None and any(texts):
        sanitizer = await create_user_text_sanitizer(db, settings)
    if effective_masking_source(document) == "override":
        segmenter = None
    for _ in range(_MAP_MERGE_ATTEMPTS):
        await _require_row(db, document)
        base = await load_persisted_document_map(document.id)
        if base is None and require_existing_map:
            raise DocumentMapMissingError(f"Document {document.id} has no anonymization map")
        working = await _working_copy(db, document, base, texts)
        language = effective_masking_language(document)
        working.language = language
        gated = await gate_untrusted_texts(
            texts,
            working,
            db,
            settings,
            language=language,
            sanitizer=sanitizer,
            joined=joined,
            segmenter=segmenter,
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
    joined: bool = False,
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
    beforehand by :func:`create_user_text_sanitizer`. ``joined`` gates the
    texts in one sanitizer run (see :func:`gate_untrusted_texts`).
    """
    gated, _ = await _gate_and_merge(
        db, settings, document, texts, sanitizer, require_existing_map=False, joined=joined
    )
    return gated


async def remask_document_texts(
    db: AsyncSession,
    settings: AppSettings,
    document: Document,
    texts: Sequence[str],
    sanitizer: "PIISanitizer",
    segmenter: Optional[TextSegmenter] = None,
) -> Tuple[List[str], bool]:
    """Re-mask stored chunk texts of ``document`` for a chat turn.

    Like :func:`gate_document_texts`, but the document must already have a
    map: one that is missing, or erased while the turn runs (a reprocess or
    delete), raises :class:`DocumentMapMissingError` instead of a partial map
    being rebuilt from these texts alone. Returns the texts and whether the
    map gained entities. ``sanitizer`` must come from
    :func:`create_user_text_sanitizer`: a chunk alone lacks the text around
    it that a context word may sit in, and its stored text may carry a
    user's chunk edit. Each text of a document without a language override
    is detected per language segment, as ingestion detects the document,
    under the budget of ``segmenter``: a chat turn passes one for all its
    documents, and without one each call gets its own.
    """
    return await _gate_and_merge(
        db,
        settings,
        document,
        texts,
        sanitizer,
        require_existing_map=True,
        segmenter=segmenter or TextSegmenter(),
    )


__all__ = [
    "DocumentGoneError",
    "DocumentMapConflictError",
    "DocumentMapMissingError",
    "TextSegmenter",
    "all_placeholders_known",
    "create_user_text_sanitizer",
    "gate_document_texts",
    "gate_untrusted_texts",
    "is_letter_fragment",
    "mask_known_entities",
    "section_title_masker",
    "remask_document_texts",
    "reserve_placeholder_counters",
]
