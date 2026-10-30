"""Word boundaries that follow how each script separates its words.

Space-delimited text breaks at whitespace or ``BOUNDARY_CHARS``; scripts written
without spaces (Han, kana, Thai, Lao, Khmer, Myanmar) also break between glyphs.
"""

from __future__ import annotations

import re
import unicodedata
from bisect import bisect_left
from typing import Mapping, Tuple

from .language_resolution import (
    particle_script_ranges,
    prefix_script_ranges,
    script_table,
    unspaced_script_ranges,
)

BOUNDARY_CHARS: frozenset[str] = frozenset(" \t\n\r.,;:!?()[]{}\"'/-")

_JOINERS: frozenset[str] = frozenset("\u200c\u200d")

# Unspaced scripts the language_resolution table does not carry.
_EXTRA_UNSPACED_RANGES: tuple[tuple[int, int], ...] = (
    (0x1000, 0x109F),
    (0x0E80, 0x0EFF),
    (0x1780, 0x17FF),
)

_UNSPACED_RANGES = (*unspaced_script_ranges(), *_EXTRA_UNSPACED_RANGES)


def _ranges_to_class(ranges: tuple[tuple[int, int], ...]) -> str:
    """Regex character class covering the inclusive code-point ``ranges``."""
    return "[" + "".join(f"{chr(lo)}-{chr(hi)}" for lo, hi in ranges) + "]"


_UNSPACED_CLASS = _ranges_to_class(_UNSPACED_RANGES)
_UNSPACED_RE = re.compile(_UNSPACED_CLASS)
# Characters below this are never unspaced: one comparison skips the regex.
_FIRST_UNSPACED = chr(min(lo for lo, _ in _UNSPACED_RANGES))
_BOUNDARY_RE = re.compile("[" + re.escape("".join(sorted(BOUNDARY_CHARS))) + "]")

# ``\b`` treats ideographs as word characters; these also accept an unspaced
# glyph next to a token edge, so "请联系a@b.cn获取" still matches.
WORD_START = rf"(?:\b|(?<={_UNSPACED_CLASS}))"
WORD_END = rf"(?:\b|(?={_UNSPACED_CLASS}))"

_PARTICLE_CLASS = _ranges_to_class(particle_script_ranges())
# A token may also end before a particle written onto it ("2019-0412입니다").
PARTICLE_START = rf"(?={_PARTICLE_CLASS})"

# Letters of the scripts whose words touch other tokens: unspaced, particle
# and prefix scripts. A token edge may lie against any of them; a digit of
# those scripts never makes one, so a run of Arabic-Indic or Thai digits stays
# a single number. Every such script lies above ``_TOUCHING_FLOOR``, a cheap
# first check that keeps the large class off ASCII and digit-dense text.
_TOUCHING_RANGES = (*_UNSPACED_RANGES, *particle_script_ranges(), *prefix_script_ranges())
_TOUCHING_CLASS = _ranges_to_class(_TOUCHING_RANGES)
_TOUCHING_FLOOR = f"[{chr(min(lo for lo, _ in _TOUCHING_RANGES))}-\U0010ffff]"
_TOUCHING_BEFORE = rf"(?<={_TOUCHING_FLOOR})(?<={_TOUCHING_CLASS})(?<!\d)"
_TOUCHING_AFTER = rf"(?={_TOUCHING_FLOOR})(?={_TOUCHING_CLASS})(?!\d)"

# ``\b`` made script-aware, for the edges of an identifier whose first and last
# characters are word characters: the value may also be written against a
# letter of a touching script ("电话138-1234-5678请", "010-1234-5678입니다",
# "و1012345678"), but not against a Latin, Cyrillic or other spaced-script
# letter, a digit or "_", so a digit run inside a longer alphanumeric word is
# still no identifier. Case-insensitive patterns keep the edge case-sensitive.
TOKEN_START = rf"(?-i:\b|{_TOUCHING_BEFORE})"
TOKEN_END = rf"(?-i:\b|{_TOUCHING_AFTER})"


def token_bounded(pattern: str) -> str:
    """Wrap a regex so it starts at :data:`TOKEN_START` and ends at :data:`TOKEN_END`."""
    return f"{TOKEN_START}(?:{pattern}){TOKEN_END}"


# The edges of a label cue are looser than a value's, since a cue is also part
# of field names ("customer_ssn", "patientSSN", "ssnNumber", "dob1") and may be
# glued to a word of another script ("ПриветSSN"). The only places that are
# not edges are between two letters of the same spaced script, so "ein"
# inside "protein" is no cue, unless that pair is a camelCase step or lies
# inside a run of capitals: all-caps text writes field names without any mark
# between the words ("PATIENTSSN", "DLNUMBER"), so a cue may start or end
# anywhere in such a run. That also reads the "EIN" of "PROTEIN" as a label,
# an accepted over-detection.
_SPACED_RANGES = tuple(
    ranges for ranges, touches in script_table().values() if not touches
)
_SAME_SPACED_SCRIPT = "|".join(
    f"(?<={_ranges_to_class(ranges)})(?={_ranges_to_class(ranges)})"
    for ranges in _SPACED_RANGES
)


def _letters_of_case(categories: frozenset[str]) -> str:
    """Regex class of the spaced-script letters whose Unicode category is in ``categories``."""
    codes = (
        code
        for ranges in _SPACED_RANGES
        for lo, hi in ranges
        for code in range(lo, hi + 1)
        if unicodedata.category(chr(code)) in categories
    )
    runs: list[tuple[int, int]] = []
    for code in codes:
        if runs and runs[-1][1] == code - 1:
            runs[-1] = (runs[-1][0], code)
        else:
            runs.append((code, code))
    return _ranges_to_class(tuple(runs))


# Cased letters of every spaced script in the table (Latin, Greek, Cyrillic),
# taken from the Unicode categories, so a case step reads the same in
# "patientSSN", "КодИНН" and "ŞSSN". Titlecase letters count as capitals.
_LOWER = _letters_of_case(frozenset({"Ll"}))
_UPPER = _letters_of_case(frozenset({"Lu", "Lt"}))
_CAMEL_STEP = f"(?<={_LOWER})(?={_UPPER})"
_CAPS_STEP = f"(?<={_UPPER})(?={_UPPER})"
CUE_EDGE = f"(?-i:{_CAMEL_STEP}|{_CAPS_STEP}|(?!{_SAME_SPACED_SCRIPT}))"
# A cue of two letters or fewer ("ID", "NO", "DL") is too short to be read at
# a case step: it would turn "NOVEMBER", "PAID" and "PROVIDER" into labels. Its
# edges are whole-word edges only; "_", a digit or a glued letter of another
# script still counts ("customer_id", "ID号").
WORD_CUE_EDGE = f"(?!{_SAME_SPACED_SCRIPT})"

# Label cues keyed by ISO 639-1 code; every entry is a regex fragment.
CueTable = Mapping[str, Tuple[str, ...]]


def cue_alternation(table: CueTable) -> str:
    """Every entry of ``table`` as one regex alternation."""
    return "|".join(entry for entries in table.values() for entry in entries)


def _bounded_cue(pattern: str, endings: Tuple[str, ...], edge: str) -> str:
    ending = f"(?:{'|'.join(endings)})?" if endings else ""
    # The lookahead fails on the first character almost everywhere, so the
    # edge assertions run only where the cue is actually written.
    return f"(?=(?:{pattern})){edge}(?:{pattern}){ending}{edge}"


def whole_cue(pattern: str, endings: Tuple[str, ...] = ("s",)) -> str:
    """A short label cue ("SSN", "EIN") that matches only between cue edges.

    One of ``endings`` may follow it: a plural ("SSNs") by default, or a case
    ending of an inflected language passed by the table that lists the cue.
    """
    return _bounded_cue(pattern, endings, CUE_EDGE)


def word_cue(pattern: str, endings: Tuple[str, ...] = ("s",)) -> str:
    """A cue of two letters or fewer ("ID", "DL"), matched between whole-word edges only."""
    return _bounded_cue(pattern, endings, WORD_CUE_EDGE)


# Longest cue that ``literal_cue`` matches between whole-word edges only.
WORD_CUE_MAX_LETTERS = 2


def literal_cue(word: str, endings: Tuple[str, ...] = ("s",)) -> str:
    """A cue written as plain letters: :func:`word_cue` up to ``WORD_CUE_MAX_LETTERS``, :func:`whole_cue` above."""
    pattern = re.escape(word)
    if len(word) <= WORD_CUE_MAX_LETTERS:
        return word_cue(pattern, endings)
    return whole_cue(pattern, endings)


def word_final_cue(pattern: str) -> str:
    """A label cue that may end a compound ("Newborn") but must end its word ("Osborne" is none)."""
    return f"(?:{pattern}){CUE_EDGE}"


# (first code point, last code point, script, whether its words touch other tokens),
# sorted by first code point. The extra unspaced ranges each stand for their own script.
_SCRIPT_SPANS: tuple[tuple[int, int, str, bool], ...] = tuple(
    sorted(
        [
            (lo, hi, script, touches)
            for script, (ranges, touches) in script_table().items()
            for lo, hi in ranges
        ]
        + [(lo, hi, f"U+{lo:04X}", True) for lo, hi in _EXTRA_UNSPACED_RANGES]
    )
)
_SCRIPT_STARTS: tuple[int, ...] = tuple(lo for lo, _, _, _ in _SCRIPT_SPANS)


def _script_of(ch: str) -> tuple[str | None, bool]:
    """The table script of ``ch`` (``None`` outside the table) and whether its words touch other tokens."""
    code = ord(ch)
    i = bisect_left(_SCRIPT_STARTS, code + 1) - 1
    if i >= 0:
        lo, hi, script, touches = _SCRIPT_SPANS[i]
        if lo <= code <= hi:
            return script, touches
    return None, False


_PREFIX_RE = re.compile(_ranges_to_class(prefix_script_ranges()))
_PARTICLE_RE = re.compile(_PARTICLE_CLASS)


def joins_without_space(ch: str) -> bool:
    """Whether ``ch`` belongs to a script written without spaces or with particles written onto tokens.

    A word of such a script can run straight into a neighbouring token of
    another script, with nothing to mark where it ends.
    """
    return is_unspaced_char(ch) or _PARTICLE_RE.match(ch) is not None


def is_prefix_letter(ch: str) -> bool:
    """Whether ``ch`` is a letter of a script that writes prefixes onto the next token."""
    return _PREFIX_RE.match(ch) is not None


def is_glued_script_break(left: str, right: str) -> bool:
    """Whether a word boundary lies between the adjacent letters ``left`` and ``right``.

    Two letters of different scripts belong to different words when either
    script writes its words against other tokens without a space (a particle
    after a token, a prefix before it, or no spaces at all). Mixed letters of
    scripts that do not, such as look-alike Latin and Cyrillic, stay one word.
    Never before a combining mark or a zero-width (non-)joiner: it belongs to
    the glyph before it.
    """
    if _attaches_to_previous(right):
        return False
    left_script, left_touches = _script_of(left)
    right_script, right_touches = _script_of(right)
    return left_script != right_script and (left_touches or right_touches)


def word_bounded(pattern: str) -> str:
    """Wrap a regex so it matches only at script-aware word edges."""
    return f"{WORD_START}{pattern}{WORD_END}"


def is_unspaced_char(ch: str) -> bool:
    """Whether ``ch`` belongs to a script written without spaces between words."""
    return ch >= _FIRST_UNSPACED and _UNSPACED_RE.match(ch) is not None


def _attaches_to_previous(ch: str) -> bool:
    """Whether ``ch`` is a combining mark or a zero-width (non-)joiner, part of the glyph before it."""
    return ch in _JOINERS or unicodedata.category(ch).startswith("M")


def is_script_break(text: str, i: int) -> bool:
    """Whether a glyph boundary lies between ``text[i-1]`` and ``text[i]``.

    Never before a combining mark or a zero-width (non-)joiner: a vowel sign
    or tone mark belongs to the glyph it follows.
    """
    if not 0 < i < len(text):
        return False
    after = text[i]
    if not (is_unspaced_char(text[i - 1]) or is_unspaced_char(after)):
        return False
    return not _attaches_to_previous(after)


def is_span_boundary(text: str, start: int, end: int) -> bool:
    """Whether ``text[start:end]`` begins and ends on a word boundary."""
    if start < 0 or end > len(text) or start >= end:
        return False
    starts_word = (
        start == 0 or text[start - 1] in BOUNDARY_CHARS or is_script_break(text, start)
    )
    ends_word = end == len(text) or text[end] in BOUNDARY_CHARS or is_script_break(text, end)
    return starts_word and ends_word


def snap_span(text: str, start: int, end: int) -> tuple[int, int]:
    """Expand ``[start, end)`` outward to the nearest word boundaries.

    A span that starts or ends inside a word (subword tokenisation, e.g.
    "kara" in "Ankara") grows to cover the whole word. Across an unspaced
    script the span stops at the glyph edge, so a name never grows into the
    clause that follows it.
    """
    if text.isascii():
        return _snap_to_spaces(text, start, end)
    n = len(text)
    while start > 0:
        prev = text[start - 1]
        if prev in BOUNDARY_CHARS:
            break
        if (prev >= _FIRST_UNSPACED or text[start] >= _FIRST_UNSPACED) and is_script_break(
            text, start
        ):
            break
        start -= 1
    while end < n:
        cur = text[end]
        if cur in BOUNDARY_CHARS:
            break
        if (cur >= _FIRST_UNSPACED or text[end - 1] >= _FIRST_UNSPACED) and is_script_break(
            text, end
        ):
            break
        end += 1
    return start, end


def _snap_to_spaces(text: str, start: int, end: int) -> tuple[int, int]:
    """Expand ``[start, end)`` to the nearest whitespace or ``BOUNDARY_CHARS``."""
    n = len(text)
    while start > 0 and text[start - 1] not in BOUNDARY_CHARS:
        start -= 1
    while end < n and text[end] not in BOUNDARY_CHARS:
        end += 1
    return start, end


def space_boundaries(text: str) -> list[int]:
    """Sorted offsets of every whitespace or ``BOUNDARY_CHARS`` character in ``text``."""
    return [match.start() for match in _BOUNDARY_RE.finditer(text)]


def snap_between(boundaries: list[int], length: int, start: int, end: int) -> tuple[int, int]:
    """Expand ``[start, end)`` to the nearest :func:`space_boundaries` offsets, ignoring glyph edges.

    Two binary searches per span, so many spans in one long unspaced run stay cheap.
    """
    left = bisect_left(boundaries, start)
    right = bisect_left(boundaries, end)
    return (
        boundaries[left - 1] + 1 if left else 0,
        boundaries[right] if right < len(boundaries) else length,
    )


def word_count(text: str) -> int:
    """Number of words in ``text``; each glyph of an unspaced script counts as one.

    For text without unspaced scripts this equals ``len(text.split())``.
    A token that mixes unspaced glyphs with other letters counts its glyphs
    plus one for the other letters; punctuation alone adds nothing.
    """
    if text.isascii():
        return len(text.split())
    count = 0
    for token in text.split():
        glyphs = sum(
            1 for ch in token if is_unspaced_char(ch) and not _attaches_to_previous(ch)
        )
        if glyphs == 0:
            count += 1
            continue
        count += glyphs
        if any(ch.isalnum() and not is_unspaced_char(ch) for ch in token):
            count += 1
    return count
