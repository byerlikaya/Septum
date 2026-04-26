from __future__ import annotations

"""
Span processing utilities for PII detection.

Provides deduplication, expansion and merging logic for detected PII spans.
These functions are stateless and operate on lists of ``DetectedSpan`` objects.
"""

from typing import List, Set

from .spans import DetectedSpan

# Entity-type priority used as a tiebreaker inside ``_dedup_simple``.
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


# Domain-aware absorption rules: when a span of the "inner" type
# overlaps a span of the "outer" type at all (not just full
# containment), the inner span is dropped and the outer wins.
# Example: in "ANTALYA SAĞLIK MERKEZİ" the LOCATION span "Antalya"
# overlaps the ORGANIZATION_NAME span "Antalya Sağlık Merkezi".
# Without this filter, length-based dedup picks LOCATION whenever it
# starts earlier than the ORG, which strips the richer ORG label
# and emits two placeholders for one semantic unit.
_ABSORPTION_RULES: dict[str, frozenset[str]] = {
    "ORGANIZATION_NAME": frozenset({"LOCATION"}),
    "POSTAL_ADDRESS": frozenset({"LOCATION", "STREET_ADDRESS"}),
    "STREET_ADDRESS": frozenset({"LOCATION"}),
}


def absorb_overlapping_spans(spans: List[DetectedSpan]) -> List[DetectedSpan]:
    """Drop inner spans that overlap an outer span per ``_ABSORPTION_RULES``.

    Runs before ``deduplicate_spans`` so the longest-wins tiebreak does
    not silently discard the richer outer span when an inner span
    happens to start at a lower offset.
    """
    if not spans:
        return spans

    outers_by_inner: dict[str, list[DetectedSpan]] = {}
    for outer_type, inner_types in _ABSORPTION_RULES.items():
        outer_spans = [s for s in spans if s.entity_type == outer_type]
        if not outer_spans:
            continue
        for inner_type in inner_types:
            outers_by_inner.setdefault(inner_type, []).extend(outer_spans)

    if not outers_by_inner:
        return spans

    kept: List[DetectedSpan] = []
    for span in spans:
        outers = outers_by_inner.get(span.entity_type)
        if outers and any(
            not (span.end <= o.start or span.start >= o.end) for o in outers
        ):
            continue
        kept.append(span)
    return kept


def deduplicate_spans(
    spans: List[DetectedSpan],
    high_priority_types: Set[str],
) -> List[DetectedSpan]:
    """Deduplicate overlapping spans with priority for sensitive identifiers.

    High-priority entity types (for example ``PHONE_NUMBER``, ``NATIONAL_ID``,
    ``IBAN``) always win over more generic entities such as PERSON or
    LOCATION when their spans overlap. Non-overlapping low-priority spans
    are still preserved.

    Tiebreaks inside the high-priority pool prefer: larger span, then
    higher entity-type priority (see ``_ENTITY_TYPE_PRIORITY``), then
    higher recognizer score. Without the type-priority step, a loose
    10-digit PHONE_NUMBER pattern at 0.80 would silently beat a
    NATIONAL_ID detector at 0.60 on the exact same offsets.
    """

    if not spans:
        return []

    high_priority = [s for s in spans if s.entity_type in high_priority_types]
    low_priority = [s for s in spans if s.entity_type not in high_priority_types]

    def _dedup_simple(candidates: List[DetectedSpan]) -> List[DetectedSpan]:
        ordered = sorted(
            candidates,
            key=lambda s: (
                s.start,
                -(s.end - s.start),
                -_ENTITY_TYPE_PRIORITY.get(s.entity_type, 50),
                -s.score,
            ),
        )
        chosen: List[DetectedSpan] = []
        for span in ordered:
            overlaps = any(
                not (span.end <= c.start or span.start >= c.end) for c in chosen
            )
            if not overlaps:
                chosen.append(span)
        return chosen

    high_dedup = _dedup_simple(high_priority)

    filtered_low: List[DetectedSpan] = []
    for span in low_priority:
        overlaps_high = any(
            not (span.end <= h.start or span.start >= h.end) for h in high_dedup
        )
        if not overlaps_high:
            filtered_low.append(span)

    low_dedup = _dedup_simple(filtered_low)

    combined = sorted(high_dedup + low_dedup, key=lambda s: s.start)
    return combined


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
        while idx > 0 and not text[idx - 1].isspace():
            idx -= 1
        return idx

    def _find_token_end(idx: int) -> int:
        n = len(text)
        while idx < n and not text[idx].isspace():
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

        # Look right for a candidate surname (one token only).
        right = end
        n = len(text)
        while right < n and text[right].isspace():
            right += 1
        if right < n:
            right_end = _find_token_end(right)
            right_token = text[right:right_end]
            if (
                _is_name_like_token(right, right_end)
                and len(right_token) >= 2
                and right_token.isalpha()
            ):
                overlaps = any(
                    not (right_end <= s_start or right >= s_end)
                    for s_start, s_end in occupied_ranges
                )
                if not overlaps:
                    end = right_end

        # Look left for a preceding name token (one token only).
        left = start
        while left > 0 and text[left - 1].isspace():
            left -= 1
        if left > 0:
            left_start = _find_token_start(left - 1)
            left_token = text[left_start:left]
            if (
                _is_name_like_token(left_start, left)
                and len(left_token) >= 2
                and left_token.isalpha()
            ):
                overlaps = any(
                    not (left <= s_start or left_start >= s_end)
                    for s_start, s_end in occupied_ranges
                )
                if not overlaps:
                    start = left_start

        expanded.append(
            DetectedSpan(
                start=start,
                end=end,
                entity_type=span.entity_type,
                score=span.score,
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
        j = i + 1
        while j < len(person_spans):
            nxt = person_spans[j]
            gap = text[end : nxt.start]
            if gap.strip() == "" and "\n" not in gap and "\r" not in gap:
                end = nxt.end
                max_score = max(max_score, nxt.score)
                j += 1
            else:
                break
        merged_person.append(
            DetectedSpan(
                start=start,
                end=end,
                entity_type="PERSON_NAME",
                score=max_score,
            )
        )
        i = j

    combined = others + merged_person
    return sorted(combined, key=lambda s: s.start)
