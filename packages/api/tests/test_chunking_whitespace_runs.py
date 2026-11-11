"""Long whitespace runs must not make boundary scoring quadratic."""

from __future__ import annotations

import random
import time

from septum_core.text_boundaries import BOUNDARY_CHARS, is_script_break

from septum_api.services.chunking.cutting import (
    _LEVEL_SENTENCE,
    _LEVEL_WHITESPACE,
    _LEVEL_WORD,
    _SENTENCE_END,
    Cutter,
)

_SPACELESS_END = frozenset("。！？")


def _reference_level(text: str, i: int) -> int:
    prev = text[i - 1]
    if prev == "\n" or prev in _SPACELESS_END:
        return _LEVEL_SENTENCE
    if prev.isspace():
        j = i - 2
        while j >= 0 and text[j].isspace():
            if text[j] == "\n":
                return _LEVEL_SENTENCE
            j -= 1
        if j >= 0 and text[j] in _SENTENCE_END:
            return _LEVEL_SENTENCE
        return _LEVEL_WHITESPACE
    if prev in BOUNDARY_CHARS or is_script_break(text, i):
        return _LEVEL_WORD
    return 0


def test_level_matches_the_unmemoised_definition() -> None:
    rng = random.Random(7)
    alphabet = "ab. \n\t!x "
    text = "".join(rng.choice(alphabet) for _ in range(3000))
    cutter = Cutter(text, [], len, 10_000)
    offsets = list(range(1, len(text) + 1))
    rng.shuffle(offsets)
    for i in offsets:
        assert cutter._level(i) == _reference_level(text, i)


def _packing_work(n: int) -> tuple[int, float]:
    """Calls to ``str.isspace`` made through the cutter's text while packing, and wall time."""
    calls = 0

    class _Char(str):
        __slots__ = ()

        def isspace(self) -> bool:
            nonlocal calls
            calls += 1
            return str.isspace(self)

    class _Text(str):
        __slots__ = ()

        def __getitem__(self, key):
            item = str.__getitem__(self, key)
            return _Char(item) if isinstance(key, int) else item

    text = _Text("start" + " " * n + "end")
    cutter = Cutter(text, [], len, 10_000)
    started = time.perf_counter()
    ranges = cutter.pack(0, len(text), [], 1_000, 100)
    elapsed = time.perf_counter() - started
    assert ranges[0][0] == 0 and ranges[-1][1] == len(text)
    return calls, elapsed


def test_a_long_whitespace_run_is_packed_in_linear_time() -> None:
    small_calls, _ = _packing_work(20_000)
    large_calls, elapsed = _packing_work(80_000)
    assert small_calls > 0
    assert large_calls / small_calls < 6
    assert elapsed < 20


def test_a_long_whitespace_run_is_memoised_in_constant_space() -> None:
    text = "start" + " " * 1_000_000 + "end"
    cutter = Cutter(text, [], len, 20_000)
    ranges = cutter.pack(0, len(text), [], 800, 200)
    assert ranges[0][0] == 0 and ranges[-1][1] == len(text)
    assert len(cutter._run_lo) <= 2
