from __future__ import annotations

"""
Span processing utilities for PII detection.

Provides deduplication, expansion and merging logic for detected PII spans.
These functions are stateless and operate on lists of ``DetectedSpan`` objects.
"""

from typing import List, Set


def deduplicate_spans(
    spans: "List[DetectedSpan]",
    high_priority_types: Set[str],
) -> "List[DetectedSpan]":
    """Deduplicate overlapping spans with priority for sensitive identifiers.

    High-priority entity types (for example ``PHONE_NUMBER``, ``NATIONAL_ID``,
    ``IBAN``) always win over more generic entities such as PERSON or
    LOCATION when their spans overlap. Non-overlapping low-priority spans
    are still preserved.
    """
    from .sanitizer import DetectedSpan  # deferred to avoid circular imports

    if not spans:
        return []

    high_priority = [s for s in spans if s.entity_type in high_priority_types]
    low_priority = [s for s in spans if s.entity_type not in high_priority_types]

    def _dedup_simple(candidates: List[DetectedSpan]) -> List[DetectedSpan]:
        ordered = sorted(
            candidates,
            key=lambda s: (s.start, -(s.end - s.start), -s.score),
        )
        chosen: List[DetectedSpan] = []
        current_end = -1
        for span in ordered:
            if span.start >= current_end:
                chosen.append(span)
                current_end = span.end
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
    spans: "List[DetectedSpan]",
) -> "List[DetectedSpan]":
    """Expand PERSON_NAME spans to include adjacent capitalized tokens.

    When a PERSON_NAME span covers only part of a name (for example, a given
    name without the following surname), this helper inspects the immediate
    neighbouring tokens on both sides and extends the span to include them
    when they look like name tokens (letter-only, starting with uppercase).
    The heuristic is intentionally simple and language-agnostic.
    """
    from .sanitizer import DetectedSpan  # deferred to avoid circular imports

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
    spans: "List[DetectedSpan]",
) -> "List[DetectedSpan]":
    """Merge consecutive PERSON_NAME spans separated only by horizontal whitespace.

    Prevents split replacements (e.g. given name and surname as two spans)
    from producing broken placeholder insertion inside a single visual name.
    """
    from .sanitizer import DetectedSpan  # deferred to avoid circular imports

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
