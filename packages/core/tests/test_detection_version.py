"""``DETECTOR_VERSION`` is public and tracks the date grammar and its output."""

from __future__ import annotations

import hashlib
import json
import random
from typing import Literal, Tuple

import septum_core
from septum_core.date_grammar import (
    _DATE_CONTEXT_CUES,
    find_date_spans,
    grammar_fingerprint,
)
from septum_core.detection_version import DETECTOR_VERSION

_BUMP_RULE = (
    "If detection output changed for any input (new or removed matches, "
    "offsets, labels), bump DETECTOR_VERSION and pin every value below. If "
    "the output is unchanged (refactor or speed-up), keep DETECTOR_VERSION "
    "and pin only the new fingerprint. The output digest is evidence of "
    "identity, not proof: it covers a differential corpus with cue contexts "
    "(colon and whitespace-bound variants) run through find_date_spans "
    "only, while DETECTOR_VERSION also covers the recognizers and span "
    "processing, so check those separately before deciding not to bump."
)

_DATES = (
    "3/15", "15/03", "12/05–14/05", "15.03.", "15.03", "13/13", "5/10",
    "15 March", "March 15", "15 mar", "5 may", "3 Mac", "12 marc", "15 Jan.",
    "15 March 2024", "Mar 15, 2024", "March 2024", "mar 2024", "Set 2024",
    "2024-03-15T10:30:00Z", "20240315", "15/03/2024", "5/6/24", "1.2.30",
    "2024年3月15日", "3月15日", "2024년 3월 15일", "15 มีนาคม 2567",
)
_JOINERS = (
    "", " ", ":", ": ", " : ", "  ", "    ", "\t", " :  ",
    " " * 5, " " * 6, " " * 7,
    "  :", "   :", "    :", ":  ", ":   ", ":    ",
    "   :   ", "    :   ", "   :    ", "  :   ", "   :  ",
    "::", " :: ",
)
_WORDS = (
    "on 15 March 2024 March 2024 mar 2024 Mar 15, 2024 May 15 may 5 the 1 mar "
    "mar mar Jan. 15 jan. 2024 set 2024 Set 2024 March 15th, 2024 March "
    "15–24, 2024 Mac 3 15 Mar. x March 155 März 3 2024 Okt. 2024 december 31 "
    "99 April 24 people seen 3/15 2024-03-15 ocak 2020 Mayıs 2020 score 5/10 "
    "at 2.5 mg 10.10.10.10 v1.2.30 3 out of 5"
).split()


def _corpus() -> list[str]:
    cues: list[tuple[Literal["before", "after"], str]] = []
    positions: Tuple[Literal["before", "after"], ...] = ("before", "after")
    for table in _DATE_CONTEXT_CUES.values():
        for position in positions:
            words = table.get(position, ())
            cues.extend((position, w) for w in list(words)[:4])
    texts = []
    for date in _DATES:
        texts.append(f"x {date} y")
        for position, cue in cues:
            for joiner in _JOINERS:
                if position == "before":
                    texts.append(f"x {cue}{joiner}{date} y")
                else:
                    texts.append(f"x {date}{joiner}{cue} y")
    rng = random.Random(7)
    texts.extend(" ".join(rng.choice(_WORDS) for _ in range(300)) for _ in range(40))
    return texts


def _output_digest() -> str:
    spans = [find_date_spans(text) for text in _corpus()]
    return hashlib.sha256(json.dumps(spans).encode("utf-8")).hexdigest()[:16]


def test_detector_version_is_exported() -> None:
    assert septum_core.DETECTOR_VERSION == DETECTOR_VERSION
    assert "DETECTOR_VERSION" in septum_core.__all__


def test_grammar_or_output_change_requires_a_version_bump() -> None:
    assert (DETECTOR_VERSION, grammar_fingerprint(), _output_digest()) == (
        "2026.10.31.1",
        "0c8e9bd19dc18ffc",
        "e301f5106901665f",
    ), _BUMP_RULE
