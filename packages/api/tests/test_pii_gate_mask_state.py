"""Batch gating reuses one masking state and must match per-text gating.

The masking state (known placeholders plus the compiled originals pattern) is
reused across a gate call and rebuilt when the map changes. These tests pin
that a batch gives exactly the output, and leaves exactly the map, that gating
each text alone gives, including overlapping originals (prefix and
longer-match cases) and entities registered in the middle of the batch.
"""

import asyncio
import copy
import re

import pytest
from sanitizer_doubles import PatternSanitizer
from septum_api.services import pii_gate
from septum_api.services.anonymization_map import AnonymizationMap

_PERSON = re.compile(r"\bPerson\d{3}\b")
_ACCOUNT = re.compile(r"\bAcct\d{4}\b")
_AFTER_LABEL = re.compile(r"Name: ([^;\n]+)")


@pytest.fixture
def compile_calls(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Record the entity count of every originals pattern compilation."""
    calls: list[int] = []
    original_builder = pii_gate._originals_pattern

    def counting_builder(entity_map, known):
        calls.append(len(entity_map))
        return original_builder(entity_map, known)

    monkeypatch.setattr(pii_gate, "_originals_pattern", counting_builder)
    return calls


def _regex_sanitizer() -> PatternSanitizer:
    """Detects every synthetic person and account token, wherever it occurs."""
    return PatternSanitizer((_PERSON, "PERSON_NAME"), (_ACCOUNT, "ACCOUNT_NUMBER"))


def _overlapping_map() -> AnonymizationMap:
    amap = AnonymizationMap(document_id=0, language="en")
    amap.add_entity("Acme", "ORGANIZATION")
    amap.add_entity("Acme Corp", "ORGANIZATION")
    amap.add_entity("Jane Roe", "PERSON_NAME")
    amap.add_entity("Jane Roe Jr", "PERSON_NAME")
    amap.add_entity("3/15", "DATE")
    amap.add_entity("13/150", "REFERENCE_NUMBER")
    return amap


_TEXTS = [
    "Acme Corp and Acme signed.",
    "Jane Roe Jr and Jane Roe met on 13/150 and 3/15.",
    "[ORGANIZATION_1] wrote to Person101 about Acct1234.",
    "Person101 replied; Acme Corp again, Acct1234 too.",
    "Jane Roe Jr, Person202, Acct5678 and [DATE_1].",
    "No known values here.",
    "",
    "Person202 and Person101 with Acme Corp Holdings.",
]


def _snapshot(amap: AnonymizationMap) -> tuple:
    return (
        dict(amap.entity_map),
        dict(amap.token_to_placeholder),
        dict(amap.token_counter),
        dict(amap.placeholder_lookup),
        sorted(amap.blocklist),
    )


async def test_batch_gate_matches_gating_each_text_alone() -> None:
    batch_map = _overlapping_map()
    single_map = copy.deepcopy(batch_map)
    sanitizer = _regex_sanitizer()

    batch = await pii_gate.gate_untrusted_texts(
        _TEXTS, batch_map, None, None, language="en", sanitizer=sanitizer
    )
    alone = []
    for text in _TEXTS:
        [out] = await pii_gate.gate_untrusted_texts(
            [text], single_map, None, None, language="en", sanitizer=sanitizer
        )
        alone.append(out)

    assert batch == alone
    assert _snapshot(batch_map) == _snapshot(single_map)
    assert batch[0] == "[ORGANIZATION_2] and [ORGANIZATION_1] signed."
    assert batch[1] == "[PERSON_NAME_1] and [PERSON_NAME_1] met on [REFERENCE_NUMBER_1] and [DATE_1]."
    assert batch[2:] == [
        "[ORGANIZATION_1] wrote to [PERSON_NAME_2] about [ACCOUNT_NUMBER_1].",
        "[PERSON_NAME_2] replied; [ORGANIZATION_2] again, [ACCOUNT_NUMBER_1] too.",
        "[PERSON_NAME_1], [PERSON_NAME_3], [ACCOUNT_NUMBER_2] and [DATE_1].",
        "No known values here.",
        "",
        "[PERSON_NAME_3] and [PERSON_NAME_2] with [ORGANIZATION_2] Holdings.",
    ]


async def test_stable_map_compiles_originals_pattern_once(compile_calls: list[int]) -> None:
    texts = [f"Acme Corp, Jane Roe and 3/15 in text {i}." for i in range(200)]

    await pii_gate.gate_untrusted_texts(
        texts, _overlapping_map(), None, None, language="en", sanitizer=_regex_sanitizer()
    )

    assert len(compile_calls) == 1


async def test_pattern_rebuilt_only_after_the_map_changes(compile_calls: list[int]) -> None:
    texts = [
        "Plain text with Acme Corp.",
        "Person101 is new here.",
        "Person101 again and Jane Roe.",
        "Acct1234 is new here.",
        "Nothing new in Acme Corp.",
    ]

    await pii_gate.gate_untrusted_texts(
        texts, _overlapping_map(), None, None, language="en", sanitizer=_regex_sanitizer()
    )

    assert len(compile_calls) == 3


def _context_only_sanitizer() -> PatternSanitizer:
    """Detects an organization only right after a ``Name: `` label, never a bare value.

    Organizations are not blocklist-propagated, so only the known-originals pattern
    can mask a later bare occurrence.
    """
    return PatternSanitizer((_AFTER_LABEL, "ORGANIZATION"))


async def test_entity_registered_mid_batch_masks_bare_value_in_later_text() -> None:
    amap = AnonymizationMap(document_id=0, language="en")
    texts = ["Name: Zed Q", "Zed Q", "met Zed Q today, then Zed Q left"]

    out = await pii_gate.gate_untrusted_texts(
        texts, amap, None, None, language="en", sanitizer=_context_only_sanitizer()
    )

    assert out == [
        "Name: [ORGANIZATION_1]",
        "[ORGANIZATION_1]",
        "met [ORGANIZATION_1] today, then [ORGANIZATION_1] left",
    ]
    assert all("Zed Q" not in o for o in out)
    assert amap.entity_map == {"Zed Q": "[ORGANIZATION_1]"}


async def test_placeholder_minted_mid_batch_is_not_masked_again() -> None:
    amap = AnonymizationMap(document_id=0, language="en")
    texts = ["Name: Zed Q", "Name: [ORGANIZATION_1]", "Name: Yan R; Name: [ORGANIZATION_1]"]

    out = await pii_gate.gate_untrusted_texts(
        texts, amap, None, None, language="en", sanitizer=_context_only_sanitizer()
    )

    assert out[0] == "Name: [ORGANIZATION_1]"
    assert out[1] == "Name: [ORGANIZATION_1]"
    assert out[2] == "Name: [ORGANIZATION_2]; Name: [ORGANIZATION_1]"
    assert amap.entity_map == {"Zed Q": "[ORGANIZATION_1]", "Yan R": "[ORGANIZATION_2]"}


_ISOLATION_TEXTS = ["Alpha Co and Beta Co sign.", "Beta Co only.", "Alpha Co only."]


async def _gate_with(original: str, entity_type: str) -> tuple[list[str], AnonymizationMap]:
    amap = AnonymizationMap(document_id=0, language="en")
    amap.add_entity(original, entity_type)
    out = await pii_gate.gate_untrusted_texts(
        _ISOLATION_TEXTS, amap, None, None, language="en", sanitizer=_regex_sanitizer()
    )
    return out, amap


async def test_maps_do_not_leak_placeholders_into_each_other() -> None:
    out_a, map_a = await _gate_with("Alpha Co", "ORGANIZATION")
    out_b, map_b = await _gate_with("Beta Co", "LOCATION")

    assert out_a == [
        "[ORGANIZATION_1] and Beta Co sign.",
        "Beta Co only.",
        "[ORGANIZATION_1] only.",
    ]
    assert out_b == [
        "Alpha Co and [LOCATION_1] sign.",
        "[LOCATION_1] only.",
        "Alpha Co only.",
    ]
    assert map_a.entity_map == {"Alpha Co": "[ORGANIZATION_1]"}
    assert map_b.entity_map == {"Beta Co": "[LOCATION_1]"}


async def test_concurrent_gate_calls_match_each_call_run_alone() -> None:
    alone_a, _ = await _gate_with("Alpha Co", "ORGANIZATION")
    alone_b, _ = await _gate_with("Beta Co", "LOCATION")

    for _ in range(20):
        (out_a, map_a), (out_b, map_b) = await asyncio.gather(
            _gate_with("Alpha Co", "ORGANIZATION"), _gate_with("Beta Co", "LOCATION")
        )
        assert out_a == alone_a
        assert out_b == alone_b
        assert map_a.entity_map == {"Alpha Co": "[ORGANIZATION_1]"}
        assert map_b.entity_map == {"Beta Co": "[LOCATION_1]"}


async def _gate_context_only(texts: list[str], **kwargs) -> tuple[list[str], AnonymizationMap]:
    amap = AnonymizationMap(document_id=0, language="en")
    out = await pii_gate.gate_untrusted_texts(
        texts, amap, None, None, language="en", sanitizer=_context_only_sanitizer(), **kwargs
    )
    return out, amap


def _assert_masked_everywhere(out: list[str], amap: AnonymizationMap, value: str) -> None:
    placeholder = amap.entity_map[value]
    assert all(value not in text for text in out)
    assert all(placeholder in text for text in out)
    assert amap.placeholder_lookup.get(placeholder, value) == value
    assert {original for original, p in amap.entity_map.items() if p == placeholder} == {value}


async def test_value_detected_in_a_later_text_is_masked_in_an_earlier_one() -> None:
    out, amap = await _gate_context_only(["Zed Q signed the deal.", "Name: Zed Q"])

    _assert_masked_everywhere(out, amap, "Zed Q")
    assert out == ["[ORGANIZATION_1] signed the deal.", "Name: [ORGANIZATION_1]"]


async def test_value_detected_only_in_the_last_text_is_masked_in_all_earlier_ones() -> None:
    out, amap = await _gate_context_only(
        ["Zed Q signed.", "Then Zed Q paid.", "Name: Zed Q"]
    )

    _assert_masked_everywhere(out, amap, "Zed Q")
    assert out == ["[ORGANIZATION_1] signed.", "Then [ORGANIZATION_1] paid.", "Name: [ORGANIZATION_1]"]


@pytest.fixture
def mask_states(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Record every masking state built, by the entity count of its map."""
    built: list[int] = []

    class CountingState(pii_gate._MaskState):
        def __init__(self, anon_map: AnonymizationMap) -> None:
            built.append(len(anon_map.entity_map))
            super().__init__(anon_map)

    monkeypatch.setattr(pii_gate, "_MaskState", CountingState)
    return built


@pytest.fixture
def mask_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every text masked against a state, premask and closing pass alike."""
    calls: list[str] = []
    original_mask = pii_gate._mask_with_state

    def counting_mask(text, state, anon_map, language=None):
        calls.append(text)
        return original_mask(text, state, anon_map, language)

    monkeypatch.setattr(pii_gate, "_mask_with_state", counting_mask)
    return calls


def _seeded_map() -> AnonymizationMap:
    amap = AnonymizationMap(document_id=0, language="en")
    amap.add_entity("Zed Q", "ORGANIZATION")
    return amap


@pytest.mark.parametrize(
    "texts, seeded, expected, states",
    [
        (["", ""], False, ["", ""], 0),
        (["Plain words."], False, ["Plain words."], 1),
        (["Plain words.", "", "More plain words."], False, ["Plain words.", "", "More plain words."], 1),
        (
            ["Zed Q again.", "Name: Zed Q"],
            True,
            ["[ORGANIZATION_1] again.", "Name: [ORGANIZATION_1]"],
            1,
        ),
    ],
)
async def test_closing_pass_is_skipped_when_nothing_new_was_detected(
    mask_states: list[int],
    mask_calls: list[str],
    texts: list[str],
    seeded: bool,
    expected: list[str],
    states: int,
) -> None:
    amap = _seeded_map() if seeded else AnonymizationMap(document_id=0, language="en")
    out = await pii_gate.gate_untrusted_texts(
        texts, amap, None, None, language="en", sanitizer=_context_only_sanitizer()
    )

    assert out == expected
    assert len(mask_states) == states
    assert len(mask_calls) == sum(1 for text in texts if text)


@pytest.mark.parametrize(
    "texts, expected",
    [
        (["Name: Zed Q"], ["Name: [ORGANIZATION_1]"]),
        (["Name: Zed Q", "", "Zed Q again."], ["Name: [ORGANIZATION_1]", "", "[ORGANIZATION_1] again."]),
    ],
)
async def test_closing_pass_runs_once_per_text_when_the_map_grew(
    mask_states: list[int], mask_calls: list[str], texts: list[str], expected: list[str]
) -> None:
    out, _ = await _gate_context_only(texts)

    non_empty = sum(1 for text in texts if text)
    assert out == expected
    assert mask_states == [0, 1]
    assert len(mask_calls) == 2 * non_empty


async def test_value_detected_once_in_a_single_text_is_masked_at_every_mention() -> None:
    out, amap = await _gate_context_only(["Name: Zed Q; met Zed Q again"])

    _assert_masked_everywhere(out, amap, "Zed Q")
    assert out == ["Name: [ORGANIZATION_1]; met [ORGANIZATION_1] again"]


async def test_value_detected_only_in_the_first_text_is_masked_at_its_later_mention_there() -> None:
    out, amap = await _gate_context_only(["Name: Zed Q; met Zed Q again", "Zed Q later."])

    _assert_masked_everywhere(out, amap, "Zed Q")
    assert out == ["Name: [ORGANIZATION_1]; met [ORGANIZATION_1] again", "[ORGANIZATION_1] later."]


async def test_closing_pass_reuses_the_last_state_when_it_is_still_current(
    mask_states: list[int],
) -> None:
    out, _ = await _gate_context_only(["Zed Q signed.", "Name: Zed Q", "Nothing here."])

    assert out == ["[ORGANIZATION_1] signed.", "Name: [ORGANIZATION_1]", "Nothing here."]
    assert mask_states == [0, 1]


async def test_segmented_gate_runs_the_closing_pass() -> None:
    out, amap = await _gate_context_only(
        ["Zed Q signed the deal.", "Name: Zed Q"], segmenter=pii_gate.TextSegmenter()
    )

    _assert_masked_everywhere(out, amap, "Zed Q")


def _organization_map() -> AnonymizationMap:
    amap = AnonymizationMap(document_id=0, language="en")
    amap.add_entity("Quinn Hale", "ORGANIZATION")
    return amap


def test_masker_returns_texts_unchanged_when_the_map_did_not_change() -> None:
    amap = _organization_map()
    masker = pii_gate.LearnedValueMasker(amap)

    assert not masker.learned
    assert masker.mask(["Ask Quinn about it", "Quinn Hale"]) == ["Ask Quinn about it", "Quinn Hale"]


def test_masker_sees_growth_in_the_token_map_alone() -> None:
    amap = _organization_map()
    masker = pii_gate.LearnedValueMasker(amap)
    entity_map = dict(amap.entity_map)

    amap.add_entity("Quinn Hale", "PERSON_NAME")

    assert amap.entity_map == entity_map
    assert masker.learned
    assert masker.mask(["Ask Quinn about it", ""]) == ["Ask [ORGANIZATION_1] about it", ""]


def test_masker_builds_one_pattern_for_every_text(compile_calls: list[int]) -> None:
    amap = AnonymizationMap(document_id=0, language="en")
    masker = pii_gate.LearnedValueMasker(amap)
    amap.add_entity("Zed Q", "ORGANIZATION")

    first = masker.mask([f"Zed Q signed text {i}." for i in range(20)])
    second = masker.mask(["Zed Q again."])

    assert all("Zed Q" not in text for text in first + second)
    assert compile_calls == [1]
