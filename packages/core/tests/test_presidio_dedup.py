"""Compare the linearithmic ``remove_duplicates`` with Presidio's quadratic one.

Presidio's filter checked every result against every kept result, so a
recognizer that matched every few characters (IPv4, 12-digit IDs, IRD and
Medicare numbers) took seconds on a few tens of kilobytes. The replacement
must return the same results in the same order, both on raw result lists
built to produce ties and through the real recognizers, and must stay fast on
the digit-dense input that exposed the slowdown.
"""

from __future__ import annotations

import importlib
import random
import time

import pytest
from presidio_analyzer import EntityRecognizer, RecognizerResult
from septum_core.presidio_dedup import PRESIDIO_REMOVE_DUPLICATES, remove_duplicates
from septum_core.recognizers.appi.recognizers import get_recognizers as appi_recognizers
from septum_core.recognizers.australia_pa.recognizers import (
    get_recognizers as australia_recognizers,
)
from septum_core.recognizers.gdpr.recognizers import get_recognizers as gdpr_recognizers
from septum_core.recognizers.nzpa.recognizers import get_recognizers as nzpa_recognizers

_DIGIT_DENSE = {
    "gdpr_ipv4": "1.2.3.4 ",
    "appi_my_number": "1234 5678 9012 ",
    "nzpa_ird": "12-345-678 ",
    "australia_pa_medicare": "1234 56789 0 ",
}
_TEXT_LENGTH = 80_000
_CPU_BUDGET_SECONDS = 1.5


@pytest.fixture(autouse=True)
def _detector_imported() -> None:
    """Importing the detector installs the fast filter into Presidio."""
    importlib.import_module("septum_core.detector")


def _recognizers() -> dict:
    packs = gdpr_recognizers() + appi_recognizers() + nzpa_recognizers() + australia_recognizers()
    return {r.name: r for r in packs if r.name in _DIGIT_DENSE}


def _random_results(rng: random.Random, count: int) -> list[RecognizerResult]:
    results = []
    for _ in range(count):
        start = rng.randint(0, 30)
        results.append(
            RecognizerResult(
                entity_type=rng.choice(["A", "B", "C"]),
                start=start,
                end=start + rng.randint(0, 8),
                score=rng.choice([0, 0.0, 0.3, 0.6, 0.6, 1, 1.0]),
            )
        )
    return results


@pytest.mark.parametrize("seed", range(300))
def test_matches_presidio_on_random_results(seed: int) -> None:
    rng = random.Random(seed)
    results = _random_results(rng, rng.randint(0, 60))

    expected = PRESIDIO_REMOVE_DUPLICATES(list(results))
    actual = remove_duplicates(list(results))

    assert [id(r) for r in actual] == [id(r) for r in expected]


def _wide_results(rng: random.Random, count: int) -> list[RecognizerResult]:
    """Sparse starts over a large range, many types, nested spans and tied scores."""
    types = [f"T{i}" for i in range(rng.randint(1, 12))]
    results = []
    for _ in range(count):
        start = rng.choice([rng.randint(0, 5000), rng.randint(0, 40) * 125])
        length = rng.choice([0, 1, rng.randint(0, 20), rng.randint(0, 2000)])
        results.append(
            RecognizerResult(
                entity_type=rng.choice(types),
                start=start,
                end=start + length,
                score=rng.choice([0, 0.2, 0.5, 0.5, 0.9, 1, 1.0]),
            )
        )
    # Re-emit some spans with another score and as separate equal objects, so a
    # kept span meets an identical span with a lower score and exact duplicates.
    for source in rng.sample(results, min(len(results), count // 4)):
        results.append(
            RecognizerResult(
                entity_type=source.entity_type,
                start=source.start,
                end=source.end,
                score=rng.choice([0.1, source.score, 1 - source.score]),
            )
        )
    rng.shuffle(results)
    return results


@pytest.mark.parametrize("seed", range(60))
def test_matches_presidio_on_wide_sparse_results(seed: int) -> None:
    rng = random.Random(seed)
    results = _wide_results(rng, rng.randint(0, 400))

    expected = PRESIDIO_REMOVE_DUPLICATES(list(results))
    actual = remove_duplicates(list(results))

    assert [id(r) for r in actual] == [id(r) for r in expected]


def test_matches_presidio_on_negative_offsets() -> None:
    results = [
        RecognizerResult("A", -5, -3, 0.5),
        RecognizerResult("A", 0, -2, 0.5),
        RecognizerResult("A", -1, -2, 0.4),
    ]

    expected = PRESIDIO_REMOVE_DUPLICATES(list(results))
    actual = remove_duplicates(list(results))

    assert [id(r) for r in actual] == [id(r) for r in expected]


def test_install_replaces_presidio_filter() -> None:
    assert EntityRecognizer.remove_duplicates is remove_duplicates


def _random_text(rng: random.Random) -> str:
    alphabet = "0123456789" * 4 + ".- \n" + "ab"
    return "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 400)))


def _spans(results: list[RecognizerResult]) -> list[tuple]:
    return [(r.entity_type, r.start, r.end, r.score) for r in results]


@pytest.mark.parametrize("seed", range(40))
def test_recognizers_match_presidio_filter(seed: int, monkeypatch: pytest.MonkeyPatch) -> None:
    rng = random.Random(seed)
    texts = [_random_text(rng) for _ in range(5)]
    recognizers = _recognizers().values()

    fast = [_spans(r.analyze(t, r.supported_entities)) for r in recognizers for t in texts]
    monkeypatch.setattr(
        EntityRecognizer, "remove_duplicates", staticmethod(PRESIDIO_REMOVE_DUPLICATES)
    )
    slow = [_spans(r.analyze(t, r.supported_entities)) for r in recognizers for t in texts]

    assert fast == slow


@pytest.mark.parametrize("name", sorted(_DIGIT_DENSE))
def test_digit_dense_input_stays_fast(name: str) -> None:
    recognizer = _recognizers()[name]
    unit = _DIGIT_DENSE[name]
    text = unit * (_TEXT_LENGTH // len(unit))

    started = time.process_time()
    results = recognizer.analyze(text, recognizer.supported_entities)
    EntityRecognizer.remove_duplicates(results)
    elapsed = time.process_time() - started

    assert len(results) >= _TEXT_LENGTH // (2 * len(unit))
    assert elapsed < _CPU_BUDGET_SECONDS
