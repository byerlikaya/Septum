from __future__ import annotations

"""
Stateless placement and slicing for the contextual detection layer.

Turns the values a local model reports for a slice of text into spans at
exact character offsets (:func:`reply_spans`), and cuts long text into
overlapping slices that fit a byte budget (:func:`contextual_slices`).
No network call, setting or refusal lives here: the Ollama adapter in
:mod:`septum_api.services.sanitizer` owns the calls, the budget and the
refusals.
"""

import re
import unicodedata
from functools import lru_cache
from typing import Iterable, List, Optional

from septum_core.spans import DetectedSpan
from septum_core.text_boundaries import (
    BOUNDARY_CHARS,
    is_glued_script_break,
    is_prefix_letter,
    is_script_break,
    is_span_boundary,
    is_unspaced_char,
    snap_span,
)
from septum_core.text_utils import (
    APOSTROPHES,
    lower_preserving_offsets,
    normalize_unicode,
)

_OLLAMA_ID_LIKE_TYPES: frozenset[str] = frozenset({"BIOMETRIC_ID", "DNA_PROFILE"})


def _is_implausible_semantic_value(etype: str, text_str: str) -> bool:
    """Reject ID-like values without digits.

    A value is never judged a heading by its shape: scripts without case
    equal their own upper case, and an all-caps diagnosis or a list item
    on its own line looks exactly like a heading. Masking a heading the
    model reports costs a word of context; dropping a real value leaks it.
    """
    return etype in _OLLAMA_ID_LIKE_TYPES and not any(c.isdigit() for c in text_str)


@lru_cache(maxsize=4096)
def _fold_char(ch: str) -> str:
    """Map a lowercase letter to the lowercase of its upper case when that is one letter."""
    round_trip = ch.upper().lower()
    return round_trip if len(round_trip) == 1 else ch


def _fold_case(text: str) -> str:
    """Case-fold ``text`` without changing its length, the same way on both sides of a match.

    On top of :func:`lower_preserving_offsets` (dotted capital ``İ`` becomes
    ``i``), a letter whose upper case lowers to another letter is folded to
    that letter, so dotless ``ı`` matches the ``I`` of an upper-cased value
    and final ``ς`` matches ``σ``. A letter whose upper case is longer
    (``ß`` to ``SS``) stays as it is.
    """
    lowered = lower_preserving_offsets(text)
    if lowered.isascii():
        return lowered
    return "".join(_fold_char(ch) for ch in lowered)


_APOSTROPHE_TO_ASCII = str.maketrans(dict.fromkeys(APOSTROPHES, "'"))
_WORD_GAP = r"[\W_]+"
_MIN_WORD_LETTERS = 3


def _match_fold(text: str) -> str:
    """:func:`_fold_case` with every apostrophe variant made ASCII; the length never changes."""
    return _fold_case(text).translate(_APOSTROPHE_TO_ASCII)


def _word_tokens(text: str) -> List[str]:
    """Runs of letters, digits and combining marks in ``text``; any other character separates them."""
    tokens: List[str] = []
    current: List[str] = []
    for ch in text:
        if unicodedata.category(ch)[0] in "LNM":
            current.append(ch)
        elif current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    return tokens


def _is_word_sized(value: str) -> bool:
    """Whether ``value`` has at least three letters, or any letter of a script written without spaces.

    Only such a value may grow to the word it opens, and only such a word of
    a value is masked on its own: "a" or "IV" would otherwise take
    "asthma" or "IVF" with them.
    """
    letters = [ch for ch in value if ch.isalpha()]
    return len(letters) >= _MIN_WORD_LETTERS or any(is_unspaced_char(ch) for ch in letters)


def _value_pattern(folded_value: str) -> Optional[re.Pattern[str]]:
    """Regex for the words of an already folded value, with any run of other characters between them.

    Spacing, dashes, apostrophe and quote variants and punctuation between
    words do not matter ("type-2" finds "type 2", "Crohn’s" finds "Crohn's",
    "diabetes (T2DM)" finds "diabetes T2DM"); punctuation before the first or
    after the last word is not part of the value.
    """
    words = _word_tokens(folded_value)
    if not words:
        return None
    return re.compile(_WORD_GAP.join(re.escape(word) for word in words))


_STROKE_LETTERS = str.maketrans(
    "đĐðÐłŁƚȽøØħĦŧŦƀɃɨƗƶƵǥǤɍɌɉɈʉɄȼȻɇɆ",
    "dDdDlLlLoOhHtTbBiIzZgGrRjJuUcCeE",
)
"""Letters written with a stroke or bar (and eth), which have no canonical decomposition, to their base letter.

One character to one, so the index map of :func:`_bare` stays valid.
"""


def _bare(text: str) -> tuple[str, List[int]]:
    """``text`` folded without its combining marks, and the index in ``text`` of each kept character.

    Each character is decomposed, stripped of its combining marks and
    recomposed, so "ş" becomes "s" and "ή" becomes "η" while a Hangul
    syllable stays one character. A letter with a stroke or bar, which
    Unicode does not decompose, becomes its base letter
    (:data:`_STROKE_LETTERS`), so "đái tháo đường" finds "dai thao duong".
    """
    if text.isascii():
        return _match_fold(text), list(range(len(text)))
    chars: List[str] = []
    index: List[int] = []
    for position, ch in enumerate(text):
        base = "".join(c for c in unicodedata.normalize("NFD", ch) if not unicodedata.combining(c))
        for kept in unicodedata.normalize("NFC", base).translate(_STROKE_LETTERS):
            chars.append(kept)
            index.append(position)
    return _match_fold("".join(chars)), index


class _MatchText:
    """A text with its folded form and, built on first use, its :func:`_bare` form."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.folded = _match_fold(text)
        self._bare: Optional[tuple[str, List[int]]] = None

    def bare(self) -> tuple[str, List[int]]:
        """The text folded without combining marks, with the index map back to :attr:`text`."""
        if self._bare is None:
            self._bare = _bare(self.text)
        return self._bare


def _original_span(text: str, index: List[int], start: int, end: int) -> tuple[int, int]:
    """Map ``[start, end)`` of a :func:`_bare` form back to ``text``, with any marks after it."""
    original_end = index[end - 1] + 1
    while original_end < len(text) and unicodedata.combining(text[original_end]):
        original_end += 1
    return index[start], original_end


def _opens_word(text: str, start: int) -> bool:
    """Whether a hit may begin at ``start``: at a word edge, or after a letter a prefix script writes onto the word."""
    if start == 0:
        return True
    prev = text[start - 1]
    return (
        prev in BOUNDARY_CHARS
        or is_script_break(text, start)
        or is_glued_script_break(prev, text[start])
        or is_prefix_letter(prev)
    )


def _bounded_hits(
    text: str, hits: Iterable[tuple[int, int]], widen: bool
) -> List[tuple[int, int]]:
    """The hits that are occurrences in ``text``, each widened to whole words when ``widen``.

    A word-bounded hit is kept as it is. With ``widen``, a hit that opens a
    word but runs into a particle or suffix (``당뇨병입니다``, ``diyabetli``),
    or that follows a prefix written onto the word (``بالسكري``), grows to
    the whole token. A hit buried inside a longer word of a spaced script
    ("HIV" in "ARCHIVE") is no occurrence.
    """
    found: dict[tuple[int, int], None] = {}
    for start, end in hits:
        if is_span_boundary(text, start, end):
            found[(start, end)] = None
        elif widen and _opens_word(text, start):
            found[snap_span(text, start, end)] = None
    return list(found)


def _value_hits(
    target: _MatchText, haystack: str, index: Optional[List[int]], needle: str, widen: bool
) -> List[tuple[int, int]]:
    """Occurrences in ``target.text`` of the folded ``needle`` found in ``haystack``.

    ``index`` maps ``haystack`` offsets back to the text; ``None`` when they
    are the same.
    """
    pattern = _value_pattern(needle)
    if pattern is None:
        return []
    hits = (match.span() for match in pattern.finditer(haystack))
    if index is not None:
        hits = (_original_span(target.text, index, start, end) for start, end in hits)
    return _bounded_hits(target.text, hits, widen)


def _token_offsets(target: _MatchText, bare_value: str) -> tuple[List[tuple[int, int]], bool]:
    """Word-bounded occurrences of each word of ``bare_value``, and whether every word was found.

    Only a word of :func:`_is_word_sized` size is looked for on its own; a
    shorter one ("Li" of "Li, Mao") is never found, so a value that has one
    is never complete. A word buried inside a longer word ("Krebs" in
    "Lungenkrebs") is no occurrence: in a spaced script it cannot be told
    from an accidental substring ("HIV" in "ARCHIVE"), and growing it would
    mask a word the model never named.
    """
    bare_text, index = target.bare()
    found: dict[tuple[int, int], None] = {}
    complete = True
    for token in dict.fromkeys(_word_tokens(bare_value)):
        if not _is_word_sized(token):
            complete = False
            continue
        token_found = False
        for match in re.finditer(re.escape(token), bare_text):
            start, end = _original_span(target.text, index, *match.span())
            if is_span_boundary(target.text, start, end):
                found[(start, end)] = None
                token_found = True
        complete = complete and token_found
    return list(found), complete


def _placed_offsets(target: _MatchText, value: str) -> tuple[List[tuple[int, int]], bool]:
    """Offsets in ``target.text`` of the occurrences of ``value``, and whether the whole value was placed.

    1. The folded value, with any run of non-word characters between its
       words (:func:`_value_pattern`).
    2. The same on both sides with combining marks removed, so "seker
       hastasi" finds "şeker hastası" and "ΔΙΑΒΗΤΗΣ" finds "Διαβήτης".
       Its hits are added to those of pass 1, so a repeat written without
       the marks is masked too.
    3. Only when neither finds the value: each word of it on its own
       (:func:`_token_offsets`). The value is placed only if every word
       was found; the words that were are still masked.

    Only a value of :func:`_is_word_sized` size grows to the word it opens
    (:func:`_bounded_hits`).
    """
    widen = _is_word_sized(value)
    exact = _value_hits(target, target.folded, None, _match_fold(value), widen)
    bare_text, index = target.bare()
    bare_value = _bare(value)[0]
    loose = _value_hits(target, bare_text, index, bare_value, widen)
    hits = list(dict.fromkeys(exact + loose))
    if hits:
        return hits, True
    return _token_offsets(target, bare_value)


def _named_type(item: dict, requested: tuple[str, ...]) -> Optional[str]:
    """The requested type an item's label names, or ``None`` when it names none of them.

    The label (``type`` or ``entity_type``) names a type up to case and
    spacing ("political opinion", "MEDICAL-CONDITION"); any other spelling
    names none.
    """
    label = item.get("type") or item.get("entity_type")
    if not label:
        return None
    normalized = re.sub(r"[\s\-]+", "_", str(label).strip().upper())
    return normalized if normalized in requested else None


def _placed_spans(
    item: object, target: _MatchText, requested: tuple[str, ...]
) -> Optional[tuple[List[DetectedSpan], bool]]:
    """Spans for one contextual-layer item and whether its whole value was placed; ``None`` for no usable value.

    A value whose label names none of the requested types, or that has no
    label, is masked under the first requested type: another type's
    placeholder still hides it and is restored locally, while dropping it
    would send it out in clear. The digit check of ID-like types applies
    only to a type the label named, however many types were requested.
    """
    if not isinstance(item, dict) or item.get("text") in (None, ""):
        return None
    value = normalize_unicode(str(item["text"])).strip()
    if not value:
        return None
    named = _named_type(item, requested)
    if named is not None and _is_implausible_semantic_value(named, value):
        return None
    etype = named or requested[0]
    offsets, complete = _placed_offsets(target, value)
    spans = [DetectedSpan(start=start, end=end, entity_type=etype, score=0.80) for start, end in offsets]
    return spans, complete


def reply_spans(
    items: List[dict], text: str, requested: tuple[str, ...]
) -> tuple[List[DetectedSpan], int, int]:
    """Place every item of one reply in ``text``: the spans, the values left unplaced, and the usable values.

    A value only partly found counts as unplaced, though its found words
    are masked. What an unplaced value means (refusal or a log line) is
    the caller's decision.
    """
    target = _MatchText(text)
    placements = [
        placed
        for placed in (_placed_spans(item, target, requested) for item in items)
        if placed is not None
    ]
    unplaced = sum(1 for _, complete in placements if not complete)
    spans = [span for found, _ in placements for span in found]
    return spans, unplaced, len(placements)


_CONTEXTUAL_SLICE_CHARS = 2000
CONTEXTUAL_SLICE_OVERLAP = 200
# A slice must hold twice the overlap in any script, at up to 4 bytes a character.
MIN_SLICE_BYTES = 8 * CONTEXTUAL_SLICE_OVERLAP


CHAT_TEMPLATE_MARGIN_TOKENS = 256
"""Tokens kept free for the model's chat template around a prompt."""


def prompt_slice_budget(empty_prompt: str, window: int, reply_tokens: int) -> Optional[int]:
    """UTF-8 bytes one slice may take so its prompt and answer fit ``window`` tokens, or ``None``.

    ``empty_prompt`` is the prompt rendered around an empty slice, ``window``
    the model's real window. A tokenizer never makes more tokens than bytes,
    so a prompt of N bytes is at most N tokens, plus the chat template
    (:data:`CHAT_TEMPLATE_MARGIN_TOKENS`) and ``reply_tokens``. Counting bytes,
    not characters, keeps dense scripts inside the window. ``None`` when the
    window cannot hold a slice of ``MIN_SLICE_BYTES``.
    """
    overhead = len(empty_prompt.encode("utf-8"))
    budget = window - reply_tokens - CHAT_TEMPLATE_MARGIN_TOKENS - overhead - 1
    return budget if budget >= MIN_SLICE_BYTES else None


def _utf8_length(ch: str) -> int:
    """UTF-8 length of one code point."""
    code = ord(ch)
    return 1 if code < 0x80 else 2 if code < 0x800 else 3 if code < 0x10000 else 4


def _slice_end(text: str, start: int, max_bytes: Optional[int]) -> int:
    """Furthest end within ``_CONTEXTUAL_SLICE_CHARS`` characters and ``max_bytes`` UTF-8 bytes of ``start``."""
    end = min(start + _CONTEXTUAL_SLICE_CHARS, len(text))
    if max_bytes is None or (end - start <= max_bytes and text[start:end].isascii()):
        return end
    used = 0
    for idx in range(start, end):
        used += _utf8_length(text[idx])
        if used > max_bytes:
            return idx
    return end


def contextual_slices(
    normalized_text: str, max_bytes: Optional[int] = None
) -> List[tuple[int, str]]:
    """Cut ``normalized_text`` into ``(offset, slice)`` pairs for the contextual prompt.

    Each slice holds at most ``_CONTEXTUAL_SLICE_CHARS`` characters and, when
    given, ``max_bytes`` UTF-8 bytes (at least ``MIN_SLICE_BYTES``, else
    ``ValueError``), and ends at the last whitespace of its second half when
    there is one more than ``CONTEXTUAL_SLICE_OVERLAP`` characters past its
    start. The next slice starts at least ``CONTEXTUAL_SLICE_OVERLAP``
    characters before that end (after a whitespace when there is one), so a
    value no longer than the overlap that crosses a cut lies whole in the
    next slice. A slice reaching past its start by more than the overlap
    (a hard cut is at least ``MIN_SLICE_BYTES / 4`` characters) makes every
    next start later than the one before.
    """
    if max_bytes is not None and max_bytes < MIN_SLICE_BYTES:
        raise ValueError(f"A slice budget of {max_bytes} bytes is below {MIN_SLICE_BYTES}.")
    length = len(normalized_text)
    slices: List[tuple[int, str]] = []
    start = 0
    while True:
        end = _slice_end(normalized_text, start, max_bytes)
        if end < length:
            low = max(start + (end - start) // 2, start + CONTEXTUAL_SLICE_OVERLAP + 1)
            cut = _last_whitespace(normalized_text, low, end)
            if cut is not None:
                end = cut
        slices.append((start, normalized_text[start:end]))
        if end >= length:
            return slices
        overlap_start = end - CONTEXTUAL_SLICE_OVERLAP
        ws = _last_whitespace(normalized_text, start + 1, overlap_start)
        start = ws + 1 if ws is not None else overlap_start


def _last_whitespace(text: str, low: int, high: int) -> Optional[int]:
    """Return the index of the last whitespace in ``text[low:high]``, or ``None``."""
    for idx in range(high - 1, low - 1, -1):
        if text[idx].isspace():
            return idx
    return None
