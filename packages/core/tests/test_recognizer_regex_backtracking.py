"""Backtracking-safety tests for the structural recognizer regexes.

Each pattern was rewritten with possessive quantifiers / atomic groups so
whitespace runs cannot be split between adjacent quantifiers, and the
structural address cue only starts a ``letters + separator + digits`` match
at the first letter of a run. The tests pin two things: the rewritten pattern
finds exactly what the previous pattern found on representative input, and a
pathological input completes in bounded time with the expected result.
"""

from __future__ import annotations

import re
import time

import pytest
from septum_core.detector import (
    CoordinatesRecognizer,
    DeviceIDRecognizer,
    HeuristicPersonNameRecognizer,
    StructuralAddressRecognizer,
)
from septum_core.recognizers.employee_id import employee_id_recognizer

_PREVIOUS = {
    "after_label": re.compile(r":\s*\n\s*([^\W\d_]{2,20})\s*(?:\n|$)", re.UNICODE),
    "label_value": re.compile(r"^(\S[^:\n]{1,40})\s*:\s*(.+)$", re.MULTILINE | re.UNICODE),
    "decimal": re.compile(
        r"-?\d{1,3}\.\d{3,8}\s*[°]?\s*[NSns]?\s*[,/;\s]\s*-?\d{1,3}\.\d{3,8}\s*[°]?\s*[EWew]?"
    ),
    "serial": re.compile(
        r"(?:serial\s*(?:number|no|#)?|s/n)\s*[=:]\s*[A-Za-z0-9]{8,20}", re.IGNORECASE
    ),
    "structural_cue": re.compile(
        r"(?:(?:BLOCK|FLAT|BLDG|UNIT|BLVD|AVE|MAH|SOK|KAT|BLK|BLV|APT|STR|CAD|AV|MH|CD|SK|ST|NO)\b\.?)"
        r"|"
        r"(?:[A-Za-z]+\s*[:./]\s*\d+)",
        re.IGNORECASE | re.UNICODE,
    ),
}

_TIME_BUDGET_SECONDS = 1.0


def _current() -> dict[str, re.Pattern[str]]:
    return {
        "after_label": HeuristicPersonNameRecognizer()._pattern_after_label,
        "label_value": StructuralAddressRecognizer()._label_value,
        "decimal": CoordinatesRecognizer()._decimal_pattern,
        "serial": DeviceIDRecognizer()._serial_pattern,
        "structural_cue": StructuralAddressRecognizer()._structural_cue,
        "employee_id_dash": employee_id_recognizer()._compiled_pattern,
        "employee_id_slash": employee_id_recognizer()._compiled_pattern,
    }


_SAMPLES = {
    "after_label": [
        "Name:\nAlice\n",
        "Owner :  \n\n  Bob  \nnext",
        "Contact:\n\tÇağrı",
        "Label:\nX\n",
        "Label:\nAbcdefghijklmnopqrstuvwxyz\n",
        "Label: Alice\n",
        "a:\n  Bo \n:\nCe",
    ],
    "label_value": [
        "Address : Main St. 12/4 NO: 5\nCity: Somewhere",
        "  indented: skipped\nLabel:\n  value on next line",
        "Very long label that keeps on going past forty chars: v",
        "Key :   \t value with: colon\n",
        "Empty:",
        "Trailing:   ",
    ],
    "decimal": [
        "41.0082, 28.9784",
        "41.0082° N, 28.9784° E",
        "-33.8688 S / 151.2093 e",
        "41.0082N 28.9784E",
        "41.0082 ° n ; -28.9784",
        "41.0082   28.9784   x",
        "1.123456789, 2.123",
        "41.0082 , ,28.9784",
    ],
    "serial": [
        "Serial Number: AB12CD34EF",
        "serial no = 12345678",
        "SERIAL#:ABCDEFGH12",
        "s/n: XYZ12345",
        "serial   :  ABCDEFGH",
        "serial nox: ABCDEFGH",
        "serial number ABCDEFGH",
    ],
    "structural_cue": [
        "Main St. 12/4 NO: 5",
        "X. 428 Y. Z NO: 10",
        "Apt 4, Flat: 7, Block.3",
        "abc:5def/6 ghi . 7",
        "xNO: 5 NOabc: 5 NO.abc: 5",
        "a:5b:6c",
        "sokak 12/3 kat:2 daire . 4",
        "ılık:5 ſt. 3 \u212aat: 4 \u0130stanbul/34",
        "çarşı : 12 éte/3 straße.9",
        "word   :   \t 42 and . x",
        "no digits here: none",
        "trailing:",
        "",
    ],
}

_PATHOLOGICAL = {
    "after_label": ":" + "\n" * 50_000 + "1",
    "label_value": "ab:" + "\n" * 50_000,
    "decimal": "1.123" + " " * 50_000 + "x",
    "serial": "serial" + " " * 50_000 + "x",
    "structural_cue": "a" * 50_000,
    "employee_id_dash": "empno-" * 20_000,
    "employee_id_slash": "EMPNO/" * 20_000,
}

# Pathological inputs that do contain a match: the time bound must hold on
# the success path too, not only when every start position fails.
_PATHOLOGICAL_MATCHING = {
    "structural_cue": ("a" * 50_000 + " : 5", [(0, 50_004)]),
}


def _spans(pattern: re.Pattern[str], text: str) -> list[tuple]:
    return [
        (m.span(), *(m.span(i) for i in range(1, (pattern.groups or 0) + 1)))
        for m in pattern.finditer(text)
    ]


@pytest.mark.parametrize(
    ("name", "text"),
    [(name, text) for name, texts in _SAMPLES.items() for text in texts],
)
def test_rewritten_pattern_keeps_previous_matches(name: str, text: str) -> None:
    previous, current = _PREVIOUS[name], _current()[name]
    if name == "after_label":
        # Only the captured name is consumed by the recognizer.
        assert [m.span(1) for m in current.finditer(text)] == [
            m.span(1) for m in previous.finditer(text)
        ]
    elif name == "label_value":
        # The recognizer strips the value and drops blank ones.
        def kept(p: re.Pattern[str]) -> list[tuple[int, int]]:
            return [m.span(2) for m in p.finditer(text) if m.group(2).strip()]

        assert kept(current) == kept(previous)
    else:
        assert _spans(current, text) == _spans(previous, text)


@pytest.mark.parametrize("name", sorted(_PATHOLOGICAL))
def test_pathological_input_completes_quickly(name: str) -> None:
    started = time.perf_counter()
    spans = [m.span() for m in _current()[name].finditer(_PATHOLOGICAL[name])]
    assert time.perf_counter() - started < _TIME_BUDGET_SECONDS
    assert spans == []


@pytest.mark.parametrize("name", sorted(_PATHOLOGICAL_MATCHING))
def test_pathological_matching_input_completes_quickly(name: str) -> None:
    text, expected = _PATHOLOGICAL_MATCHING[name]
    started = time.perf_counter()
    spans = [m.span() for m in _current()[name].finditer(text)]
    assert time.perf_counter() - started < _TIME_BUDGET_SECONDS
    assert spans == expected


def test_structural_address_recognizer_long_letter_run_completes_quickly() -> None:
    started = time.perf_counter()
    results = StructuralAddressRecognizer().analyze("Label: " + "a" * 50_000 + "\n")
    assert time.perf_counter() - started < _TIME_BUDGET_SECONDS
    assert results == []


def test_recognizers_still_detect_representative_values() -> None:
    coords = CoordinatesRecognizer().analyze("at 41.0082° N, 28.9784° E today")
    assert [(r.start, r.end) for r in coords] == [(3, 25)]

    serial = DeviceIDRecognizer().analyze("serial no = 12345678")
    assert [(r.start, r.end) for r in serial] == [(0, 20)]

    names = HeuristicPersonNameRecognizer().analyze("Name:\nAlice\n")
    assert [(r.start, r.end) for r in names] == [(6, 11)]

    address = StructuralAddressRecognizer().analyze("Address: Main St. 12/4 NO: 5\n")
    assert [(r.start, r.end) for r in address] == [(9, 28), (0, 28)]
