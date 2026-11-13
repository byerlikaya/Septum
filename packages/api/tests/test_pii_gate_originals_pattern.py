"""The originals pattern must match exactly what the flat alternation matched, at a bounded cost.

:func:`pii_gate._originals_pattern` factors the common prefixes of each group
so that many originals sharing a long prefix do not cost their number times
that prefix at every position. A differential test pins the behaviour against
the flat builder it replaced, kept here as ``_flat_reference_pattern``:
identical spans and named groups on seeded random maps and texts. The timing
tests compare the two builders in the same process, as ratios only. Originals
nesting deeper than the factoring cap are refused before anything compiles.
"""

import random
import re
import time
import tracemalloc
import unicodedata
from typing import Callable, List, Optional, Sequence, Tuple

import pytest
from septum_api.errors import OriginalsPatternTooDeepError, public_error_text
from septum_api.services import pii_gate as g
from septum_api.services.anonymization_map import AnonymizationMap
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
        ["a" * depth + "b" for depth in range(1, 100)],
        ["x" * 40 + format(i, "b") for i in range(300)] + ["z1"],
        ["KAYA", "KAY", "KA", "K.", "kaya", "Kaya", "KAYALAR", "ALİ", "ALI", "ALICI", "ADI"],
        ["3/15", "13/150", "3/1", "315", "٣/١٥", "A4711", "4711", "47", "TR33 0006 1005"],
    ],
    ids=["nested-just-under-the-depth-cap", "shared-prefix-binary", "short-tokens", "digit-edges"],
)
def test_structured_maps_match_the_flat_reference(originals: List[str]) -> None:
    """Nesting just under the depth cap and the documented edge cases agree too."""
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


_SENTINEL = "Zorblaxquin"


def _nested_chain(levels: int) -> dict:
    """``levels`` originals, each a prefix of the next: the prefix tree nests ``levels`` levels deep."""
    return {_SENTINEL + "q" * i: f"[ENTITY_{i}]" for i in range(levels)}


class _CompileSpy:
    """Stands in for ``re.compile`` and records every pattern it is asked to compile."""

    def __init__(self) -> None:
        self.calls = 0
        self._compile = re.compile

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return self._compile(*args, **kwargs)


@pytest.fixture
def compile_spy(monkeypatch: pytest.MonkeyPatch) -> _CompileSpy:
    spy = _CompileSpy()
    monkeypatch.setattr(g.re, "compile", spy)
    return spy


@pytest.mark.parametrize("levels", [g._MAX_FACTORED_DEPTH + 1, 599, 1500])
def test_nesting_deeper_than_the_cap_is_refused_before_anything_compiles(
    levels: int, compile_spy: _CompileSpy
) -> None:
    """The refusal comes while the pattern string is built: no compile, no scan, no flat fallback."""
    entity_map = _nested_chain(levels)
    started = time.perf_counter()
    with pytest.raises(OriginalsPatternTooDeepError) as refused:
        g._originals_pattern(entity_map, set(entity_map.values()))
    elapsed = time.perf_counter() - started
    assert compile_spy.calls == 0
    assert refused.value.code == "originals_pattern_too_deep"
    assert refused.value.depth == g._MAX_FACTORED_DEPTH + 1
    assert refused.value.limit == g._MAX_FACTORED_DEPTH
    assert elapsed < 1.0, elapsed


@pytest.mark.parametrize("levels", [g._MAX_FACTORED_DEPTH - 1, g._MAX_FACTORED_DEPTH])
def test_nesting_up_to_the_cap_is_masked_quickly_and_in_little_memory(levels: int) -> None:
    """``_MAX_FACTORED_DEPTH`` nested levels are the most accepted: 20,000 characters in well under a second."""
    entity_map = _nested_chain(levels)
    known = set(entity_map.values())
    text = (_SENTINEL + "q" * (levels + 5) + " ") * (20_000 // (len(_SENTINEL) + levels + 6) + 1)
    text = text[:20_000]
    re.purge()
    tracemalloc.start()
    try:
        pattern = g._originals_pattern(entity_map, known)
        matches = sum(1 for _ in pattern.finditer(text))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert matches > 0
    assert peak < 20 * 1024 * 1024, peak
    assert _best_of(3, _scan(pattern, text)) < 1.0
    assert _best_of(3, _scan(pattern, "a" * 20_000)) < 1.0
    assert _matches(pattern, text) == _matches(_flat_reference_pattern(entity_map, known), text)


def test_refusal_names_no_original() -> None:
    """Message, ``str`` and ``repr`` carry counts only."""
    entity_map = _nested_chain(600)
    with pytest.raises(OriginalsPatternTooDeepError) as refused:
        g._originals_pattern(entity_map, set(entity_map.values()))
    for text in (str(refused.value), repr(refused.value), refused.value.detail, public_error_text(refused.value)):
        assert _SENTINEL not in text
        assert "Zorblax" not in text
        assert not any(original in text for original in entity_map)


def test_a_refused_build_leaves_nothing_cached(compile_spy: _CompileSpy) -> None:
    """A later call on the same state refuses again; no half-built pattern is ever used."""
    amap = AnonymizationMap(document_id=0, language="en")
    for original in _nested_chain(g._MAX_FACTORED_DEPTH + 1):
        amap.add_entity(original, "ORGANIZATION")
    state = g._MaskState(amap)
    for _ in range(2):
        with pytest.raises(OriginalsPatternTooDeepError):
            state.originals_pattern
        assert "originals_pattern" not in vars(state)
        with pytest.raises(OriginalsPatternTooDeepError):
            g._mask_with_state(f"Paid by {_SENTINEL}q.", state, amap)
    with pytest.raises(OriginalsPatternTooDeepError):
        g.mask_known_entities(f"Paid by {_SENTINEL}q.", amap)
    assert compile_spy.calls == 0


def _state_refuses(amap: AnonymizationMap) -> bool:
    """Whether a masking state of ``amap`` refuses any pattern it compiles."""
    state = g._MaskState(amap)
    try:
        state.originals_pattern
        state.fragments_pattern
        state.protect_pattern
    except OriginalsPatternTooDeepError:
        return True
    return False


def _check_refuses(amap: AnonymizationMap) -> bool:
    try:
        g.ensure_originals_searchable(amap)
    except OriginalsPatternTooDeepError:
        return True
    return False


@pytest.mark.parametrize("seed", range(6))
@pytest.mark.parametrize(
    "levels", [0, g._MAX_FACTORED_DEPTH - 1, g._MAX_FACTORED_DEPTH, g._MAX_FACTORED_DEPTH + 1, 400]
)
def test_the_ingestion_check_refuses_exactly_what_masking_refuses(
    levels: int, seed: int, compile_spy: _CompileSpy
) -> None:
    """Same verdict as a masking state on chains around the cap, mixed with fragments and other originals.

    A chain of exactly the cap is refused once another original branches
    off above it (one more level), so at the cap only the agreement is pinned.
    """
    rng = random.Random(seed)
    amap = AnonymizationMap(document_id=0, language="en")
    for original in _nested_chain(levels):
        amap.add_entity(original, "ORGANIZATION")
    for original in _random_originals(rng):
        if original:
            amap.add_entity(original, rng.choice(["PERSON_NAME", "ID_NUMBER"]))
    if seed % 2:
        amap.add_entity("K", "GENDER")
    refused = _check_refuses(amap)
    assert compile_spy.calls == 0
    assert refused == _state_refuses(amap)
    if levels > g._MAX_FACTORED_DEPTH:
        assert refused
    elif levels < g._MAX_FACTORED_DEPTH:
        assert not refused


def test_the_ingestion_check_passes_maps_without_searchable_originals() -> None:
    amap = AnonymizationMap(document_id=0, language="en")
    g.ensure_originals_searchable(amap)
    amap.add_entity("K", "GENDER")
    g.ensure_originals_searchable(amap)
    assert not _state_refuses(amap)
