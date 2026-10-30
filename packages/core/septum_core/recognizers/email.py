"""The single e-mail address recognizer, used whatever regulations are active.

An e-mail address is personal data under every built-in regulation, so the
detector registers this recognizer next to its other format-driven baseline
recognizers, in place of Presidio's built-in ``EmailRecognizer``, and no
regulation pack ships its own copy.

Matching runs on a compatibility-folded view of the text (see
:func:`~septum_core.text_utils.compatibility_fold`): a fullwidth ``＠``, an
ideographic full stop or a zero-width character inside the address does not
hide it, and the reported span still points at the original characters.

Where an address touches other text the span errs towards masking more:

* Text written against the local part without a separator is masked with it,
  because it cannot be told apart from part of a name (``山田taro@``). The
  only exception is a single prefix letter of a script that writes prefixes
  onto the next word (``وjohn@``, "and john@").
* The domain ends where a word of another script is written against it (a
  particle after the top-level label, see
  :func:`~septum_core.text_boundaries.is_glued_script_break`). When that cut
  leaves no valid domain, the uncut run is used instead.
"""

from __future__ import annotations

import re
import unicodedata
from typing import AbstractSet, List, Optional

from presidio_analyzer import EntityRecognizer, RecognizerResult
from presidio_analyzer.nlp_engine import NlpArtifacts

from ..text_boundaries import (
    is_glued_script_break,
    is_prefix_letter,
    joins_without_space,
)
from ..text_utils import compatibility_fold

ENTITY_TYPE = "EMAIL_ADDRESS"
RECOGNIZER_NAME = "email_address"

_SCORE = 0.9

# RFC 5322 atext symbols and "." in the local part; letters, digits, marks and
# "_" come on top. The domain takes "." and "-".
_LOCAL_PUNCTUATION = frozenset(".!#$%&'*+-/=?^`{|}~")
_DOMAIN_PUNCTUATION = frozenset(".-")

# A punycode top-level label ("xn--p1ai").
_PUNYCODE_TLD_RE = re.compile(r"[Xx][Nn]--[A-Za-z0-9-]+")
# A dotted IPv4 domain ("127.0.0.1"), not part of a longer dotted number.
_IPV4_RE = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}(?!\d)(?!\.\d)")
# An address literal ("[192.168.1.1]", "[IPv6:2001:db8::1]") holding a digit.
# The digit lookahead stays inside the literal's own bounded length, so an
# unclosed "[" costs constant time.
_ADDRESS_LITERAL_RE = re.compile(
    r"\[(?:[Ii][Pp][Vv]6:)?(?=[0-9A-Fa-f:.]{0,44}\d)[0-9A-Fa-f:.]{2,45}\]"
)
# Most letters of a script written without spaces (or with particles written
# onto tokens) that the local part takes from a run glued in front of the rest
# of it: a name in such a script is 2-4 characters, and the clause before it
# stays unmasked. A local part written wholly in such a script is not capped.
GLUED_RUN_MAX_LETTERS = 4

# Most characters between the quotes of a quoted local part.
_QUOTED_LOCAL_MAX = 128

# Code points that read as "@" once folded; text without any of them holds no address.
_AT_SIGNS = ("@", "＠", "﹫")


def _is_mark(ch: str) -> bool:
    """Whether ``ch`` is a combining mark (vowel sign, tone mark, harakat), part of the glyph before it."""
    return unicodedata.category(ch).startswith("M")


def _is_word_char(ch: str) -> bool:
    return ch.isalnum() or ch == "_" or _is_mark(ch)


def _is_local_char(ch: str) -> bool:
    return _is_word_char(ch) or ch in _LOCAL_PUNCTUATION


def _is_domain_char(ch: str) -> bool:
    return _is_word_char(ch) or ch in _DOMAIN_PUNCTUATION


def _local_run_start(text: str, local_end: int) -> int:
    """First index of the local part ending at ``local_end``.

    The whole run of local-part characters is taken, leading symbols and marks
    aside, with two limits: a leading run of a script written without spaces
    keeps only its last ``GLUED_RUN_MAX_LETTERS`` letters, and one glued prefix
    letter of a prefix-attaching script stays outside.
    """
    start = local_end
    while start > 0 and _is_local_char(text[start - 1]):
        start -= 1
    while start < local_end and (text[start] in _LOCAL_PUNCTUATION or _is_mark(text[start])):
        start += 1
    start = _capped_glued_run_start(text, start, local_end)
    if (
        start + 1 < local_end
        and is_prefix_letter(text[start])
        and text[start + 1].isalpha()
        and is_glued_script_break(text[start], text[start + 1])
    ):
        start += 1
    return start


def _capped_glued_run_start(text: str, start: int, local_end: int) -> int:
    """``start`` moved to the last ``GLUED_RUN_MAX_LETTERS`` letters of the space-free run it begins.

    Only a run glued to a letter of another script is capped. A run that ends
    at the "@", or before a digit or punctuation of the local part
    (``สมชาย.ใจดี@``, ``王小明2020@``), is the local part itself and stays whole.
    """
    letters: List[int] = []
    i = start
    while i < local_end:
        ch = text[i]
        if joins_without_space(ch) and not _is_mark(ch):
            letters.append(i)
        elif not (letters and _is_mark(ch)):
            break
        i += 1
    if (
        len(letters) > GLUED_RUN_MAX_LETTERS
        and i < local_end
        and text[i].isalpha()
        and is_glued_script_break(text[letters[-1]], text[i])
    ):
        return letters[-GLUED_RUN_MAX_LETTERS]
    return start


def _quoted_local_start(text: str, at: int) -> Optional[int]:
    """Index of the opening quote of a quoted local part closing at ``text[at - 1]``."""
    i = at - 2
    floor = max(-1, at - 3 - _QUOTED_LOCAL_MAX)
    while i > floor:
        ch = text[i]
        if ch == "\n":
            return None
        if ch == '"' and (i == 0 or text[i - 1] != "\\"):
            return i if i < at - 2 else None
        i -= 1
    return None


def _local_start(text: str, at: int) -> Optional[int]:
    """Start of the local part before the "@" at ``text[at]``, or ``None`` when there is none."""
    if at > 0 and text[at - 1] == '"':
        quoted = _quoted_local_start(text, at)
        if quoted is not None:
            return quoted
        start = _local_run_start(text, at - 1)
        return start if start < at - 1 else None
    start = _local_run_start(text, at)
    # "/@" is a path segment ("https://example.com/@user"), not an address.
    if start == at or text[at - 1] == "/":
        return None
    return start


def _domain_cut(text: str, begin: int, end: int, soft_dots: AbstractSet[int]) -> int:
    """End of the domain in ``text[begin:end]``, before any glued word of another script.

    Letters are compared across digits, "_" and combining marks; a "." or "-"
    separates labels, so it resets the comparison. A full stop that was not an
    ASCII "." (``。``) ends the domain when the letters on either side of it
    belong to glued scripts: that is a sentence end, not a label separator.
    """
    left: Optional[str] = None
    for j in range(begin, end):
        ch = text[j]
        if ch.isalpha():
            if left is not None and is_glued_script_break(left, ch):
                return j
            left = ch
        elif ch.isdigit() or ch == "_" or _is_mark(ch):
            continue
        else:
            if (
                j in soft_dots
                and left is not None
                and j + 1 < end
                and text[j + 1].isalpha()
                and is_glued_script_break(left, text[j + 1])
            ):
                return j
            left = None
    return end


def _tld_end(text: str, begin: int, end: int) -> Optional[int]:
    """End of the top-level label starting at ``text[begin]``, or ``None`` when it is not one.

    A top-level label is punycode, or a letter followed by letters and the
    combining marks attached to them (``com``, ``中国``, ``भारत``), at least two
    characters long. Digits or "_" written straight after it (``com_``,
    ``com2``) are masked with it rather than leaving the address unmatched.
    """
    punycode = _PUNYCODE_TLD_RE.match(text, begin, end)
    if punycode is not None:
        stop = punycode.end()
    else:
        if begin >= end or not text[begin].isalpha():
            return None
        stop = begin + 1
        while stop < end and (text[stop].isalpha() or _is_mark(text[stop])):
            stop += 1
        if stop - begin < 2:
            return None
    while stop < end and _is_word_char(text[stop]):
        stop += 1
    return stop


def _domain_match_end(
    text: str, begin: int, end: int, skip_dots: AbstractSet[int] = frozenset()
) -> Optional[int]:
    """End of the longest domain in ``text[begin:end]`` that ends in a top-level label.

    Empty labels ("example..com") are accepted: a typo'd address is still one.
    Dots at the ``skip_dots`` positions do not start a top-level label.
    """
    best: Optional[int] = None
    dot = text.find(".", begin, end)
    while dot >= 0:
        if dot not in skip_dots:
            tld = _tld_end(text, dot + 1, end)
            if tld is not None:
                best = tld
        dot = text.find(".", dot + 1, end)
    return best


def _domain_end(text: str, at: int, soft_dots: AbstractSet[int]) -> Optional[int]:
    """End of the domain after the "@" at ``text[at]``, or ``None`` when there is none."""
    begin = at + 1
    literal = _ADDRESS_LITERAL_RE.match(text, begin)
    if literal is not None:
        return literal.end()
    end = begin
    while end < len(text) and _is_domain_char(text[end]):
        end += 1
    cut = _domain_cut(text, begin, end, soft_dots)
    found = _domain_match_end(text, begin, cut)
    # The uncut run wins when the cut leaves no domain, or when a further
    # label and top-level label follow the glued word (``a.co例子.example.com``).
    # A trailing particle (``com에게.``) or a sentence-ending ``。`` adds none.
    if cut < end and (found is None or _domain_match_end(text, cut, end, soft_dots) is not None):
        found = _domain_match_end(text, begin, end) or found
    if found is None:
        ipv4 = _IPV4_RE.match(text, begin, end)
        found = ipv4.end() if ipv4 is not None else None
    return found


def _address_around(text: str, at: int, soft_dots: AbstractSet[int]) -> Optional[tuple[int, int]]:
    """The address whose "@" is ``text[at]``, or ``None`` when there is none."""
    start = _local_start(text, at)
    if start is None:
        return None
    end = _domain_end(text, at, soft_dots)
    if end is None:
        return None
    return start, end


def find_addresses(text: str) -> List[tuple[int, int]]:
    """``(start, end)`` offsets in ``text`` of every e-mail address, in order."""
    if not any(sign in text for sign in _AT_SIGNS):
        return []
    folded, origin = compatibility_fold(text)
    soft_dots: AbstractSet[int] = (
        frozenset()
        if text.isascii()
        else frozenset(k for k, ch in enumerate(folded) if ch == "." and text[origin[k]] != ".")
    )
    found: List[tuple[int, int]] = []
    at = folded.find("@")
    while at >= 0:
        span = _address_around(folded, at, soft_dots)
        if span is not None:
            found.append((origin[span[0]], origin[span[1] - 1] + 1))
        at = folded.find("@", at + 1)
    # A quoted local part can reach back over earlier addresses: merge every overlap.
    spans: List[tuple[int, int]] = []
    for start, end in sorted(found):
        if spans and start < spans[-1][1]:
            spans[-1] = (spans[-1][0], max(end, spans[-1][1]))
        else:
            spans.append((start, end))
    return spans


class EmailAddressRecognizer(EntityRecognizer):
    """Presidio recognizer for e-mail addresses in any script."""

    def __init__(self) -> None:
        super().__init__(
            supported_entities=[ENTITY_TYPE],
            name=RECOGNIZER_NAME,
            supported_language="en",
        )

    def load(self) -> None:
        """Nothing to load: the recognizer is pattern-only."""

    def analyze(
        self,
        text: str,
        entities: List[str],
        nlp_artifacts: Optional[NlpArtifacts] = None,
    ) -> List[RecognizerResult]:
        if entities and ENTITY_TYPE not in entities:
            return []
        return [
            RecognizerResult(
                entity_type=ENTITY_TYPE,
                start=start,
                end=end,
                score=_SCORE,
                recognition_metadata={
                    RecognizerResult.RECOGNIZER_NAME_KEY: self.name,
                    RecognizerResult.RECOGNIZER_IDENTIFIER_KEY: self.id,
                },
            )
            for start, end in find_addresses(text)
        ]
