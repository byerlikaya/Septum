"""The originals pattern must match exactly what the flat alternation matched, at a bounded cost.

:func:`pii_gate._originals_pattern` factors the common prefixes of each group
so that many originals sharing a long prefix do not cost their number times
that prefix at every position. A differential test pins the behaviour against
the flat builder it replaced, kept here as ``_flat_reference_pattern``:
identical spans and named groups on seeded random maps and texts. The timing
tests compare the two builders in the same process, as ratios only.
"""

import random
import re
import time
import unicodedata
from typing import Callable, List, Optional, Sequence, Tuple

import pytest
from septum_api.services import pii_gate as g
from septum_core.text_boundaries import CAMEL_STEP, TOKEN_END, TOKEN_START


def _flat_original_alternative(original: str) -> str:
    """The pre-change digit alternative: each digit end may not touch another digit."""
    escaped = re.escape(original)
    before = r"(?<!\d)" if original[0].isdigit() else ""
    after = r"(?!\d)" if original[-1].isdigit() else ""
    return f"{before}{escaped}{after}"


def _flat_whole_token(value: str) -> str:
    """The pre-change symbol-fragment alternative: ``value`` with no word character on either side."""
    before = TOKEN_START if g._WORD_CHAR.match(value[0]) else r"(?<!\w)"
    return rf"{before}{re.escape(value)}(?!\w)"


def _flat_word_end_group(edged: Sequence[str]) -> str:
    """The pre-change word-end group: a flat alternation of short word tokens."""
    words = [o for o in edged if g._WORD_RUN.fullmatch(o)]
    if not words:
        return ""
    reach = max(len(o) for o in words)
    near_end = rf"(?=\w{{1,{reach}}}{TOKEN_END})"
    return f"{g._guarded([re.escape(o) for o in words], words, edge=near_end)}(?={TOKEN_END})"


def _flat_reference_pattern(entity_map: dict, known: set) -> Optional[re.Pattern]:
    """The pre-change ``_originals_pattern``: one flat literal alternation per group."""
    originals = [o for o in entity_map if o and not g.is_letter_fragment(o)]
    if not originals:
        return None
    by_length = g._longest_first(originals)
    with_digit = [o for o in by_length if g._has_digit(o)]
    letters_only = [o for o in by_length if not g._has_digit(o)]
    symbols = [o for o in letters_only if g._is_symbol_fragment(o)]
    words = [o for o in letters_only if not g._is_symbol_fragment(o)]
    open_left = [o for o in words if g._WORD_CHAR.match(o[0]) and g._opens_anywhere(o)]
    edged = [o for o in words if g._WORD_CHAR.match(o[0]) and not g._opens_anywhere(o)]
    unedged = [o for o in words if not g._WORD_CHAR.match(o[0])]
    edged_alternatives = [re.escape(o) for o in edged]
    edged_alternation = "|".join(edged_alternatives)
    groups = [
        g._guarded([re.escape(k) for k in g._longest_first(known)], known),
        g._guarded([_flat_original_alternative(o) for o in with_digit], with_digit),
        g._guarded([re.escape(o) for o in open_left], open_left),
        (
            f"{TOKEN_START}(?P<{g._CUE}>(?i:{g._FIELD_LABEL_CUE}))(?P<{g._GLUED}>{edged_alternation})"
            if edged
            else ""
        ),
        g._guarded(edged_alternatives, edged, edge=f"(?:{TOKEN_START}|{CAMEL_STEP})"),
        _flat_word_end_group(edged),
        "|".join(_flat_whole_token(o) for o in symbols),
        g._guarded([re.escape(o) for o in unedged], unedged),
    ]
    return re.compile("|".join(part for part in groups if part))


def _flat_protect_pattern(known: set) -> Optional[re.Pattern]:
    """The pre-change protect pattern: a flat alternation of the known placeholders."""
    if not known:
        return None
    return re.compile(g._guarded([re.escape(p) for p in g._longest_first(known)], known))


_LATIN = "abcdeiklmnorstyz"
_ALPHABETS = (
    _LATIN,
    _LATIN.upper(),
    "ıİiIşŞğĞçÇöÖüÜ",
    "äÄßéÉ",
    "абвгдАБВГД",
    "김이박李王",
    "0123456789",
    "٠١٢٣",
    "+-./_'& ",
)
_COMBINING = "́̇"
_CUES = ("SOYADI", "adı", "ALICI", "Name", "nachname", "UNVANI", "ad soyad", "ADRES")
_PLACEHOLDERS = ("[PERSON_NAME_1]", "[PERSON_NAME_12]", "[ORGANIZATION_2]", "[DATE_3]")


def _random_token(rng: random.Random, length: int) -> str:
    """A token mostly from one alphabet, sometimes mixing scripts, digits or combining marks."""
    base = rng.choice(_ALPHABETS[:-1])
    chars = []
    for _ in range(length):
        roll = rng.random()
        if roll < 0.12:
            chars.append(rng.choice(rng.choice(_ALPHABETS)))
        elif roll < 0.15 and chars:
            chars.append(rng.choice(_COMBINING))
        else:
            chars.append(rng.choice(base))
    token = "".join(chars)
    return unicodedata.normalize(rng.choice(("NFC", "NFD", "NFC")), token)


def _case_variants(value: str) -> List[str]:
    return [value.upper(), value.lower(), value.title(), value.capitalize()]


def _random_originals(rng: random.Random) -> List[str]:
    """Originals of every shape the builder sorts into its groups, with shared prefixes."""
    originals: List[str] = []
    for _ in range(rng.randint(1, 18)):
        shape = rng.random()
        if shape < 0.3:
            token = _random_token(rng, rng.randint(1, 5))
        elif shape < 0.5:
            token = _random_token(rng, rng.randint(5, 14))
        elif shape < 0.6:
            token = f"{_random_token(rng, rng.randint(2, 6))} {_random_token(rng, rng.randint(2, 6))}"
        elif shape < 0.68:
            token = rng.choice("AaBOİı") + "".join(rng.choice("+-#*") for _ in range(rng.randint(1, 3)))
        elif shape < 0.76:
            token = rng.choice("+-.(_'") + _random_token(rng, rng.randint(1, 6))
        elif shape < 0.84:
            token = "".join(rng.choice("0123456789/.-") for _ in range(rng.randint(1, 8)))
        elif shape < 0.88:
            token = rng.choice(_PLACEHOLDERS)
        elif shape < 0.92:
            token = rng.choice("aAx") * rng.randint(20, 60) + _random_token(rng, rng.randint(0, 3))
        else:
            token = _random_token(rng, rng.randint(1, 4)) + rng.choice("0123456789")
        originals.append(token)
        if originals and rng.random() < 0.5:
            source = rng.choice(originals)
            if len(source) > 1:
                originals.append(source[: rng.randint(1, len(source) - 1)])
        if rng.random() < 0.3:
            originals.append(token + _random_token(rng, rng.randint(1, 4)))
        if rng.random() < 0.25:
            originals.append(rng.choice(_case_variants(token)))
    if rng.random() < 0.05:
        originals.append(_random_token(rng, rng.randint(600, 2500)))
    if rng.random() < 0.05:
        originals.append("")
    return originals


def _random_text(rng: random.Random, originals: Sequence[str], known: Sequence[str]) -> str:
    """Text built around the originals: glued, cased, cut, labelled, between scripts and digits."""
    pieces: List[str] = []
    for _ in range(rng.randint(3, 25)):
        roll = rng.random()
        value = rng.choice(originals) if originals else ""
        if roll < 0.3:
            pieces.append(value)
        elif roll < 0.4:
            pieces.append(rng.choice(_case_variants(value)) if value else value)
        elif roll < 0.5:
            pieces.append(value[: rng.randint(0, len(value))])
        elif roll < 0.58:
            pieces.append(rng.choice(_CUES) + value)
        elif roll < 0.64 and known:
            pieces.append(rng.choice(known))
        elif roll < 0.7:
            pieces.append(rng.choice(_PLACEHOLDERS))
        elif roll < 0.8:
            pieces.append(_random_token(rng, rng.randint(1, 6)))
        else:
            pieces.append(rng.choice(_ALPHABETS[-1] + "0123456789\n\t李김ıİ"))
        if rng.random() < 0.5:
            pieces.append(rng.choice(" \n,.:;-/()" + "0123456789aZı李"))
    text = "".join(pieces)
    return unicodedata.normalize(rng.choice(("NFC", "NFD", "NFKC")), text) if rng.random() < 0.2 else text


def _matches(pattern: Optional[re.Pattern], text: str) -> List[Tuple[Tuple[int, int], tuple]]:
    if pattern is None:
        return []
    return [(m.span(), tuple(sorted(m.groupdict().items()))) for m in pattern.finditer(text)]


_MAP_SEEDS = range(500)
_TEXTS_PER_MAP = 8


def test_factored_pattern_matches_the_flat_reference() -> None:
    """Seeded random maps and texts: identical spans and named groups, case by case."""
    cases = 0
    for seed in _MAP_SEEDS:
        rng = random.Random(seed)
        originals = _random_originals(rng)
        entity_map = {o: f"[ENTITY_{i}]" for i, o in enumerate(originals)}
        known = set(entity_map.values()) | set(rng.sample(_PLACEHOLDERS, rng.randint(0, 2)))
        known.discard("")
        factored = g._originals_pattern(entity_map, known)
        reference = _flat_reference_pattern(entity_map, known)
        assert (factored is None) == (reference is None), seed
        known_list = sorted(known)
        for _ in range(_TEXTS_PER_MAP):
            text = _random_text(rng, [o for o in originals if o], known_list)
            assert _matches(factored, text) == _matches(reference, text), (seed, text)
            cases += 1
    assert cases == len(_MAP_SEEDS) * _TEXTS_PER_MAP


def test_factored_placeholder_patterns_match_the_flat_reference() -> None:
    """Known placeholders are protected, and bare letters found, exactly where the flat alternations did."""
    for seed in range(200):
        rng = random.Random(10_000 + seed)
        known = {f"[{rng.choice(('PERSON_NAME', 'DATE', 'ORG'))}_{rng.randint(1, 120)}]" for _ in range(40)}
        state = g._MaskState.__new__(g._MaskState)
        state.known = known
        text = "".join(
            rng.choice((*sorted(known), "[PERSON_NAME_", "[DATE_1", "]", "[", " ", "x", "7"))
            for _ in range(60)
        )
        assert _matches(state.protect_pattern, text) == _matches(_flat_protect_pattern(known), text)
        fragments = {"K": "[SEX_1]", "T.": "[INITIAL_1]"}
        flat_fragments = re.compile(
            f"{_flat_protect_pattern(known).pattern}|(?<![\\w.])(?:T\\.|K)(?!\\.?\\w)"
        )
        assert _matches(g._standalone_fragments_pattern(fragments, known), text + " K T. ") == _matches(
            flat_fragments, text + " K T. "
        )


@pytest.mark.parametrize(
    "originals",
    [
        ["a" * depth + "b" for depth in range(1, 260)],
        ["x" * 40 + format(i, "b") for i in range(300)] + ["z1"],
        ["KAYA", "KAY", "KA", "K.", "kaya", "Kaya", "KAYALAR", "ALİ", "ALI", "ALICI", "ADI"],
        ["3/15", "13/150", "3/1", "315", "٣/١٥", "A4711", "4711", "47", "TR33 0006 1005"],
    ],
    ids=["nested-beyond-the-depth-cap", "shared-prefix-binary", "short-tokens", "digit-edges"],
)
def test_structured_maps_match_the_flat_reference(originals: List[str]) -> None:
    """Prefix chains deeper than the factoring cap and the documented edge cases agree too."""
    entity_map = {o: f"[ENTITY_{i}]" for i, o in enumerate(originals)}
    known = set(entity_map.values())
    factored = g._originals_pattern(entity_map, known)
    reference = _flat_reference_pattern(entity_map, known)
    rng = random.Random(7)
    for _ in range(150):
        text = _random_text(rng, originals, sorted(known))
        assert _matches(factored, text) == _matches(reference, text), text
    for text in ("a" * 300, "x" * 2000, "SOYADKAYA1990 VALİ ALICIALİ imzaKAYA", "13/150 3/15 IBANTR33 0006 1005"):
        assert _matches(factored, text) == _matches(reference, text), text


def _best_of(runs: int, action: Callable[[], object]) -> float:
    """Shortest wall time of ``runs`` calls, the least noisy reading on a loaded machine."""
    best = float("inf")
    for _ in range(runs):
        started = time.perf_counter()
        action()
        best = min(best, time.perf_counter() - started)
    return best


def _scan(pattern: re.Pattern, text: str) -> Callable[[], object]:
    return lambda: sum(1 for _ in pattern.finditer(text))


def _shared_prefix_map(count: int) -> dict:
    """Originals sharing a 40-character prefix, plus one that keeps the engine from factoring it."""
    originals = ["a" * 40 + "b" + str(i) for i in range(count)] + ["z1"]
    return {o: f"[ENTITY_{i}]" for i, o in enumerate(originals)}


def test_shared_prefix_originals_cost_far_less_than_the_flat_alternation() -> None:
    """600 originals sharing a 40-character prefix against 8,000 matching characters."""
    entity_map = _shared_prefix_map(600)
    factored = g._originals_pattern(entity_map, set())
    reference = _flat_reference_pattern(entity_map, set())
    text = "a" * 8000
    factored_time = reference_time = float("inf")
    for _ in range(3):
        reference_time = min(reference_time, _best_of(1, _scan(reference, text)))
        factored_time = min(factored_time, _best_of(3, _scan(factored, text)))
    assert reference_time / factored_time >= 20, (reference_time, factored_time)


def test_shared_prefix_cost_grows_linearly_with_the_text() -> None:
    """Doubling the adversarial text roughly doubles the factored pattern's time."""
    factored = g._originals_pattern(_shared_prefix_map(600), set())
    single = double = float("inf")
    for _ in range(5):
        single = min(single, _best_of(3, _scan(factored, "a" * 8000)))
        double = min(double, _best_of(3, _scan(factored, "a" * 16000)))
    assert double / single < 3.5, (single, double)


def test_benign_maps_are_not_slower_than_the_flat_alternation() -> None:
    """Building, compiling and scanning ordinary names stays within 1.5x of the flat builder."""
    rng = random.Random(42)
    originals = {
        f"{_random_token(rng, rng.randint(3, 9)).title()} {_random_token(rng, rng.randint(3, 9)).title()}"
        for _ in range(3000)
    }
    entity_map = {o: f"[PERSON_NAME_{i}]" for i, o in enumerate(sorted(originals))}
    known = set(entity_map.values())
    sample = sorted(originals)[:200]
    texts = [
        " ".join(rng.choice(sample) if rng.random() < 0.05 else _random_token(rng, rng.randint(2, 9)) for _ in range(2500))
        for _ in range(3)
    ]

    def run(builder: Callable[[dict, set], Optional[re.Pattern]]) -> Callable[[], object]:
        def action() -> int:
            re.purge()
            pattern = builder(entity_map, known)
            return sum(1 for text in texts for _ in pattern.finditer(text))

        return action

    factored_time = reference_time = float("inf")
    for _ in range(3):
        reference_time = min(reference_time, _best_of(1, run(_flat_reference_pattern)))
        factored_time = min(factored_time, _best_of(1, run(g._originals_pattern)))
    assert factored_time / reference_time < 1.5, (reference_time, factored_time)
