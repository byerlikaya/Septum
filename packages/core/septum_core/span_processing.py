from __future__ import annotations

"""
Span processing utilities for PII detection.

Provides deduplication, expansion and merging logic for detected PII spans.
These functions are stateless and operate on lists of ``DetectedSpan`` objects.
"""

import unicodedata
from dataclasses import dataclass
from typing import List, Set

from .spans import DetectedSpan, clip_core
from .text_boundaries import is_script_break
from .text_utils import APOSTROPHES

# Entity-type priority used as a tiebreaker inside ``deduplicate_spans``.
# Higher number wins when two spans cover exactly the same offsets.
# The intent is to stop loose numeric recognizers (PHONE_NUMBER) from
# capturing identifiers that deserve a more specific label
# (NATIONAL_ID, IBAN, credit card). The absolute numbers here are not
# significant — only the relative ordering matters.
_ENTITY_TYPE_PRIORITY: dict[str, int] = {
    "CREDIT_CARD_NUMBER": 100,
    "IBAN": 100,
    "CPF": 95,
    "NATIONAL_ID": 95,
    "SOCIAL_SECURITY_NUMBER": 95,
    "HEALTH_INSURANCE_ID": 90,
    "MEDICAL_RECORD_NUMBER": 90,
    "PASSPORT_NUMBER": 90,
    "TAX_ID": 85,
    "DRIVERS_LICENSE": 80,
    "LICENSE_PLATE": 75,
    "PHONE_NUMBER": 70,
}


# Winner order for partial overlaps, used after ``_ENTITY_TYPE_PRIORITY``.
# Structured identifiers that a generic layer may only fragment (an email
# or URL that a NER PERSON or LOCATION span clips) outrank the generic
# types. Types not listed here default to 50.
_PARTIAL_OVERLAP_PRIORITY: dict[str, int] = {
    "EMAIL_ADDRESS": 60,
    "URL": 55,
    "IP_ADDRESS": 55,
    "MAC_ADDRESS": 55,
}


# Domain-aware absorption rules: when a span of the "inner" type
# overlaps a span of the "outer" type, the overlapped characters are
# removed from the inner span and the outer wins. The inner part that
# lies outside the outer span is kept, so no character goes unmasked.
# Example: in "ANTALYA SAĞLIK MERKEZİ" the LOCATION span "Antalya"
# overlaps the ORGANIZATION_NAME span "Antalya Sağlık Merkezi".
# Without this filter, length-based dedup picks LOCATION whenever it
# starts earlier than the ORG, which strips the richer ORG label
# and emits two placeholders for one semantic unit.
_ABSORPTION_RULES: dict[str, frozenset[str]] = {
    "DATE_OF_BIRTH": frozenset({"DATE"}),
    "ORGANIZATION_NAME": frozenset({"LOCATION"}),
    "POSTAL_ADDRESS": frozenset({"LOCATION", "STREET_ADDRESS"}),
    "STREET_ADDRESS": frozenset({"LOCATION"}),
}


def absorb_overlapping_spans(
    spans: List[DetectedSpan], text: str | None = None
) -> List[DetectedSpan]:
    """Trim inner spans around the outer spans that absorb them (``_ABSORPTION_RULES``).

    An inner span fully inside an outer span yields no remainder. A partly
    overlapping inner span keeps the part outside the outer span under its
    own type, so the outer span stays whole and the remainder is still
    masked. When ``text`` is given, offsets are clamped to it and remainders
    get the same trim and token fold as ``deduplicate_spans``. Spans with
    ``start >= end`` are discarded.

    Rules run one layer per outer type, in ``_ABSORPTION_RULES`` order. The
    order that matters is POSTAL_ADDRESS before STREET_ADDRESS: a STREET_ADDRESS
    span is both an inner span of POSTAL_ADDRESS and an outer span of LOCATION,
    so POSTAL_ADDRESS trims it first and only then does it absorb its own inner
    LOCATION spans. Runs before ``deduplicate_spans`` so the longest-wins
    tiebreak does not split the richer outer span when an inner span happens
    to start at a lower offset.
    """
    cleaned = _clamped(spans, text)
    for outer_type, inner_types in _ABSORPTION_RULES.items():
        cleaned = _absorb_layer(cleaned, outer_type, inner_types, text)
    return cleaned


@dataclass
class _Grow:
    """A winner range that may widen over a token tail; ``core`` is the text it names."""

    start: int
    end: int
    core: tuple[int, int]

    def to_span(self, entity_type: str, score: float) -> DetectedSpan:
        """The span over this range, carrying ``core``."""
        return _make(self.start, self.end, entity_type, score, self.core)


def _absorb_layer(
    spans: List[DetectedSpan],
    outer_type: str,
    inner_types: frozenset[str],
    text: str | None,
) -> List[DetectedSpan]:
    """Let every span of ``outer_type`` absorb the overlapping ``inner_types`` spans."""
    grown = {
        id(s): _Grow(s.start, s.end, _source_core(s))
        for s in spans
        if s.entity_type == outer_type
    }
    if not grown:
        return spans
    winners = list(grown.values())

    # An inner span handled before a later fold widens its outer would keep a
    # remainder that now overlaps it, so the pass repeats until no outer grows.
    # Each repeat only widens outers toward the text, so it terminates.
    while True:
        before = [(w.start, w.end) for w in winners]
        remainders: dict[int, List[DetectedSpan]] = {}
        for index, span in enumerate(spans):
            if span.entity_type not in inner_types:
                continue
            cuts = [
                (w.start, w.end)
                for w in winners
                if _overlaps(span.start, span.end, w.start, w.end)
            ]
            if not cuts:
                remainders[index] = [span]
                continue
            pieces = _subtract(span.start, span.end, cuts)
            if text is not None:
                pieces = [_claim_tails(text, s, e, winners) for s, e in pieces]
            remainders[index] = _remainders(span, pieces, text)
        if [(w.start, w.end) for w in winners] == before:
            break

    result: List[DetectedSpan] = []
    for index, span in enumerate(spans):
        if span.entity_type == outer_type:
            result.append(grown[id(span)].to_span(span.entity_type, span.score))
        elif index in remainders:
            result.extend(remainders[index])
        else:
            result.append(span)
    return result


def deduplicate_spans(
    spans: List[DetectedSpan],
    high_priority_types: Set[str],
    text: str | None = None,
) -> List[DetectedSpan]:
    """Deduplicate overlapping spans without losing any covered character.

    High-priority entity types (for example ``PHONE_NUMBER``, ``NATIONAL_ID``,
    ``IBAN``) always win over more generic entities such as PERSON or
    LOCATION when their spans overlap.

    Spans with ``start >= end`` are discarded. When ``text`` is given, offsets
    are first clamped to ``[0, len(text)]``. Spans are then admitted in the
    order: high-priority pool first, then start offset, then larger span, then
    entity-type priority (see ``_ENTITY_TYPE_PRIORITY``), then recognizer score.
    A span fully covered by spans already kept is dropped. A span that only
    partly overlaps kept spans keeps its uncovered remainder under its own
    type, so no alphanumeric character of any input span is left unmasked.
    A newcomer that outranks every span it overlaps on ``_partial_rank`` (for
    example an EMAIL beating a PERSON fragment, or a NATIONAL_ID beating a
    PHONE) is kept whole and the overlapped spans are trimmed around it.
    Overlapping spans of one entity type merge into a single union span with
    the highest score. When ``text`` is given, remainders are trimmed of
    leading and trailing non-alphanumeric characters and dropped if nothing
    alphanumeric is left. A remainder that cuts inside a token (the "nın" of
    "Kaya'nın" after a PERSON span ends at "Kaya") is folded into the winner
    up to the token boundary instead of becoming a separate span, provided
    the folded part opens with a letter (a digit run stays separate). The fold
    widens the masked range only: the winner's ``core`` keeps the text it had
    before the fold, which is what the anonymization map registers.

    A loose 10-digit PHONE_NUMBER pattern at 0.80 must not silently beat a
    NATIONAL_ID detector at 0.60 on the exact same offsets, which is why the
    entity-type priority sits before the score in the order above.
    """

    cleaned = _clamped(spans, text)
    ordered = sorted(
        cleaned,
        key=lambda s: (
            0 if s.entity_type in high_priority_types else 1,
            s.start,
            -(s.end - s.start),
            -_ENTITY_TYPE_PRIORITY.get(s.entity_type, 50),
            -s.score,
        ),
    )
    chosen: List[DetectedSpan] = []
    for span in ordered:
        _admit(chosen, span, high_priority_types, text)
    return sorted(chosen, key=lambda s: s.start)


def _clamped(spans: List[DetectedSpan], text: str | None) -> List[DetectedSpan]:
    """Copy ``spans`` with offsets clamped to ``text`` and empty ones dropped."""
    limit = len(text) if text is not None else None
    cleaned: List[DetectedSpan] = []
    for span in spans:
        start, end = span.start, span.end
        if limit is not None:
            start = min(max(start, 0), limit)
            end = min(max(end, 0), limit)
        if start < end:
            cleaned.append(
                _make(start, end, span.entity_type, span.score, _source_core(span))
            )
    return cleaned


def _partial_rank(
    span: DetectedSpan, high_priority_types: Set[str]
) -> tuple[bool, int]:
    priority = _ENTITY_TYPE_PRIORITY.get(
        span.entity_type, _PARTIAL_OVERLAP_PRIORITY.get(span.entity_type, 50)
    )
    return (span.entity_type in high_priority_types, priority)


def _subtract(
    start: int, end: int, cuts: List[tuple[int, int]]
) -> List[tuple[int, int]]:
    """Return the parts of ``[start, end)`` not covered by any cut range."""
    pieces: List[tuple[int, int]] = []
    cursor = start
    for cut_start, cut_end in sorted(cuts):
        if cut_end <= cursor or cut_start >= end:
            continue
        if cut_start > cursor:
            pieces.append((cursor, cut_start))
        cursor = max(cursor, cut_end)
    if cursor < end:
        pieces.append((cursor, end))
    return pieces


def _is_mark(ch: str) -> bool:
    """A combining mark (category M), which belongs to the character before it."""
    return unicodedata.category(ch).startswith("M")


def _overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    """Whether the half-open ranges ``[a_start, a_end)`` and ``[b_start, b_end)`` meet."""
    return not (a_end <= b_start or a_start >= b_end)


def _source_core(span: DetectedSpan) -> tuple[int, int]:
    """The range that names ``span``'s entity: its ``core``, or the whole span."""
    return clip_core(span.start, span.end, span.core)


def _make(
    start: int,
    end: int,
    entity_type: str,
    score: float,
    core: tuple[int, int],
) -> DetectedSpan:
    """Build a span over ``[start, end)`` whose ``core`` is clipped to it.

    The core is dropped when it covers the whole span or nothing of it.
    """
    core_start, core_end = clip_core(start, end, core)
    if core_start >= core_end or (core_start, core_end) == (start, end):
        return DetectedSpan(start=start, end=end, entity_type=entity_type, score=score)
    return DetectedSpan(
        start=start,
        end=end,
        entity_type=entity_type,
        score=score,
        core=(core_start, core_end),
    )


def _joins_through_apostrophe(text: str, a: int) -> bool:
    """Whether the apostrophe at ``text[a]`` sits inside a token.

    It does when a word character lies on each side with no script break
    between them, so "Kaya'nın" joins and "Z’请" (an unspaced glyph after the
    apostrophe) does not. The apostrophe is never at the text edge.
    """
    return (
        0 < a < len(text) - 1
        and text[a] in APOSTROPHES
        and (text[a - 1].isalnum() or _is_mark(text[a - 1]))
        and (text[a + 1].isalnum() or _is_mark(text[a + 1]))
        and not is_script_break(text, a)
        and not is_script_break(text, a + 1)
    )


def _joins(text: str, p: int) -> bool:
    """Whether the edge before ``text[p]`` lies inside one token.

    A token is a run of word characters with no script break between them.
    An apostrophe between two word characters joins a suffix to its name
    ("Kaya'nın"), the same convention ``strip_possessive_suffix`` uses.
    """
    if not 0 < p < len(text):
        return False
    left, right = text[p - 1], text[p]
    if (left.isalnum() or _is_mark(left)) and (right.isalnum() or _is_mark(right)):
        return not is_script_break(text, p)
    if right in APOSTROPHES:
        return _joins_through_apostrophe(text, p)
    if left in APOSTROPHES:
        return _joins_through_apostrophe(text, p - 1)
    return False


def _claim_tails(
    text: str | None,
    start: int,
    end: int,
    winners: List[_Grow],
) -> tuple[int, int]:
    """Fold the part of ``[start, end)`` that continues a winner's token into it.

    ``winners`` are mutable :class:`_Grow` ranges; only ``start`` and ``end``
    are widened, ``core`` is left alone. A winner ending at
    ``start`` takes the leading tail of the token it cuts, and a winner
    starting at ``end`` takes the trailing one, up to the token boundary.
    A tail is folded only when it opens with a letter (see
    ``_tail_starts_with_letter``): a digit run after an apostrophe stays its
    own span. When several winners share a boundary the first one in
    ``winners`` takes the tail. Returns the part left over, which may be
    empty. Without ``text`` nothing is folded.
    """
    if text is None:
        return start, end
    left = next((w for w in winners if w.end == start), None)
    if (
        left is not None
        and _joins(text, start)
        and _tail_starts_with_letter(text, start)
    ):
        q = start + 1
        while q < end and _joins(text, q):
            q += 1
        left.end = q
        start = q
    right = next((w for w in winners if w.start == end), None)
    if right is not None and start < end and _joins(text, end):
        k = end - 1
        while k > start and _joins(text, k):
            k -= 1
        if _tail_starts_with_letter(text, k):
            right.start = k
            end = k
    return start, end


def _tail_starts_with_letter(text: str, p: int) -> bool:
    """Whether the tail beginning at ``text[p]`` opens with a letter.

    An apostrophe may come first ("’nın"); the letter must follow it. A
    letter is ``isalpha`` or a combining mark, so a digit run does not count.
    """
    if p < len(text) and text[p] in APOSTROPHES:
        p += 1
    return p < len(text) and (text[p].isalpha() or _is_mark(text[p]))


def _remainders(
    span: DetectedSpan,
    ranges: List[tuple[int, int]],
    text: str | None,
) -> List[DetectedSpan]:
    result: List[DetectedSpan] = []
    for start, end in ranges:
        if text is not None:
            while start < end and not (text[start].isalnum() or _is_mark(text[start])):
                start += 1
            while end > start and not (text[end - 1].isalnum() or _is_mark(text[end - 1])):
                end -= 1
        if start >= end or (
            text is not None and not any(ch.isalnum() for ch in text[start:end])
        ):
            continue
        result.append(
            _make(start, end, span.entity_type, span.score, _source_core(span))
        )
    return result


def _admit(
    chosen: List[DetectedSpan],
    span: DetectedSpan,
    high_priority_types: Set[str],
    text: str | None,
) -> None:
    overlapped = [c for c in chosen if _overlaps(span.start, span.end, c.start, c.end)]
    if not overlapped:
        chosen.append(span)
        return
    overlapped_ids = {id(c) for c in overlapped}

    if all(c.entity_type == span.entity_type for c in overlapped):
        members = [span, *overlapped]
        cores = [_source_core(m) for m in members]
        chosen[:] = [c for c in chosen if id(c) not in overlapped_ids]
        chosen.append(
            _make(
                min(m.start for m in members),
                max(m.end for m in members),
                span.entity_type,
                max(m.score for m in members),
                (min(c[0] for c in cores), max(c[1] for c in cores)),
            )
        )
        return

    newcomer_rank = _partial_rank(span, high_priority_types)
    if all(newcomer_rank > _partial_rank(c, high_priority_types) for c in overlapped):
        winner = _Grow(span.start, span.end, _source_core(span))
        kept = [c for c in chosen if id(c) not in overlapped_ids]
        for c in overlapped:
            for start, end in _subtract(c.start, c.end, [(span.start, span.end)]):
                start, end = _claim_tails(text, start, end, [winner])
                kept.extend(_remainders(c, [(start, end)], text))
        kept.append(winner.to_span(span.entity_type, span.score))
        chosen[:] = kept
        return

    winners = [_Grow(c.start, c.end, _source_core(c)) for c in overlapped]
    gaps = [
        _claim_tails(text, start, end, winners)
        for start, end in _subtract(
            span.start, span.end, [(c.start, c.end) for c in overlapped]
        )
    ]
    chosen[:] = [c for c in chosen if id(c) not in overlapped_ids] + [
        w.to_span(c.entity_type, c.score) for w, c in zip(winners, overlapped)
    ]
    chosen.extend(_remainders(span, gaps, text))


def expand_person_name_spans(
    text: str,
    spans: List[DetectedSpan],
) -> List[DetectedSpan]:
    """Expand PERSON_NAME spans to include adjacent capitalized tokens.

    When a PERSON_NAME span covers only part of a name (for example, a given
    name without the following surname), this helper inspects the immediate
    neighbouring tokens on both sides and extends the span to include them
    when they look like name tokens (letter-only, starting with uppercase).
    The heuristic is intentionally simple and language-agnostic.
    """
    if not spans or not text:
        return spans

    def _find_token_start(idx: int) -> int:
        while idx > 0 and not text[idx - 1].isspace() and not is_script_break(text, idx):
            idx -= 1
        return idx

    def _find_token_end(idx: int) -> int:
        n = len(text)
        while idx < n and not text[idx].isspace() and not is_script_break(text, idx):
            idx += 1
        return idx

    def _is_name_like_token(start: int, end: int) -> bool:
        token = text[start:end]
        if not token:
            return False
        if not token[0].isalpha() or not token[0].isupper():
            return False
        if any(ch.isdigit() or ch == "_" for ch in token):
            return False
        return True

    def _scan_horizontal_ws(idx: int, step: int) -> tuple[int, bool]:
        """Skip horizontal whitespace from ``idx`` in ``step`` direction.

        Returns the post-scan index and whether a newline was hit. Form
        labels always sit on a line above/below their value, so any
        expansion crossing ``\\n`` would absorb the wrong row.
        """
        limit = len(text) if step > 0 else 0
        peek = (lambda i: i) if step > 0 else (lambda i: i - 1)
        while (step > 0 and idx < limit) or (step < 0 and idx > limit):
            ch = text[peek(idx)]
            if not ch.isspace():
                break
            if ch in ("\n", "\r"):
                return idx, True
            idx += step
        return idx, False

    expanded: list[DetectedSpan] = []
    occupied_ranges = [(s.start, s.end) for s in spans]

    for span in spans:
        if span.entity_type != "PERSON_NAME":
            expanded.append(span)
            continue

        start = span.start
        end = span.end

        original_span_text = text[start:end].strip()
        if not _is_name_like_token(start, end) or len(original_span_text) < 2:
            expanded.append(span)
            continue

        # A side where the core stops short of the span carries a folded
        # suffix ("’nın"): that side is the end of the name, so nothing is
        # added past it.
        core_start, core_end = _source_core(span)

        # Look right for a candidate surname (one token only).
        right, crossed_nl = _scan_horizontal_ws(end, 1)
        if core_end == span.end and not crossed_nl and right < len(text):
            right_end = _find_token_end(right)
            right_token = text[right:right_end]
            if (
                _is_name_like_token(right, right_end)
                and len(right_token) >= 2
                and right_token.isalpha()
            ):
                overlaps = any(
                    _overlaps(right, right_end, s_start, s_end)
                    for s_start, s_end in occupied_ranges
                )
                if not overlaps:
                    end = right_end

        # Look left for a preceding name token (one token only).
        left, crossed_nl = _scan_horizontal_ws(start, -1)
        if core_start == span.start and not crossed_nl and left > 0:
            left_start = _find_token_start(left - 1)
            left_token = text[left_start:left]
            if (
                _is_name_like_token(left_start, left)
                and len(left_token) >= 2
                and left_token.isalpha()
            ):
                overlaps = any(
                    _overlaps(left_start, left, s_start, s_end)
                    for s_start, s_end in occupied_ranges
                )
                if not overlaps:
                    start = left_start

        expanded.append(
            _make(
                start,
                end,
                span.entity_type,
                span.score,
                (start if start < span.start else core_start,
                 end if end > span.end else core_end),
            )
        )

    expanded_sorted = sorted(
        expanded, key=lambda s: (s.start, -(s.end - s.start), -s.score)
    )
    return expanded_sorted


def merge_adjacent_person_name_spans(
    text: str,
    spans: List[DetectedSpan],
) -> List[DetectedSpan]:
    """Merge consecutive PERSON_NAME spans separated only by horizontal whitespace.

    Prevents split replacements (e.g. given name and surname as two spans)
    from producing broken placeholder insertion inside a single visual name.
    """
    if not spans:
        return spans

    others = [s for s in spans if s.entity_type != "PERSON_NAME"]
    person_spans = sorted(
        [s for s in spans if s.entity_type == "PERSON_NAME"],
        key=lambda s: s.start,
    )
    if len(person_spans) <= 1:
        return sorted(spans, key=lambda s: s.start)

    merged_person: list[DetectedSpan] = []
    i = 0
    while i < len(person_spans):
        cur = person_spans[i]
        start, end = cur.start, cur.end
        max_score = cur.score
        last = cur
        j = i + 1
        while j < len(person_spans):
            nxt = person_spans[j]
            gap = text[end : nxt.start]
            # A folded suffix ends the name: no merge past it, and no merge
            # into a span whose suffix sits before its core.
            if (
                _source_core(last)[1] < last.end
                or _source_core(nxt)[0] > nxt.start
            ):
                break
            if gap.strip() == "" and "\n" not in gap and "\r" not in gap:
                end = nxt.end
                max_score = max(max_score, nxt.score)
                last = nxt
                j += 1
            else:
                break
        merged_person.append(
            _make(
                start,
                end,
                "PERSON_NAME",
                max_score,
                (_source_core(cur)[0], _source_core(last)[1]),
            )
        )
        i = j

    combined = others + merged_person
    return sorted(combined, key=lambda s: s.start)
