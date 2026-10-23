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


def _second_document_map() -> AnonymizationMap:
    amap = AnonymizationMap(document_id=1, language="en")
    amap.add_entity("Acme Corp", "ORGANIZATION")
    amap.add_entity("Acme", "ORGANIZATION")
    amap.add_entity("Jane Roe", "PERSON_NAME")
    amap.add_entity("3/15", "DATE")
    return amap


_CHUNK_TEXTS = [
    "Acme Corp and Acme met Jane Roe Jr on 13/150 and 3/15.",
    "Jane Roe Jr only, plus Person101 and Acct1234.",
    "No known values here.",
    "",
    "Acme Corp Holdings and 3/15 again.",
]


def test_masker_compiles_originals_pattern_once_for_many_chunks(
    compile_calls: list[int],
) -> None:
    masker = pii_gate.KnownEntityMasker(_overlapping_map())
    texts = [f"Acme Corp, Jane Roe and 3/15 in chunk {i}." for i in range(20)]

    masked = [masker.mask(text) for text in texts]

    assert len(compile_calls) == 1
    assert masked == [pii_gate.mask_known_entities(t, _overlapping_map()) for t in texts]


def test_masker_matches_per_chunk_masking_across_two_document_maps() -> None:
    maps = {0: _overlapping_map(), 1: _second_document_map()}
    reference_maps = {0: _overlapping_map(), 1: _second_document_map()}
    maskers: dict[int, pii_gate.KnownEntityMasker] = {}
    chunk_plan = [(doc_id, text) for text in _CHUNK_TEXTS for doc_id in (0, 1)] * 3

    for doc_id, text in chunk_plan:
        masker = maskers.setdefault(id(maps[doc_id]), pii_gate.KnownEntityMasker(maps[doc_id]))
        expected = pii_gate.mask_known_entities(text, reference_maps[doc_id])
        assert masker.mask(text) == expected

    assert maps[0].entity_map == reference_maps[0].entity_map
    assert maps[1].entity_map == reference_maps[1].entity_map


def test_masker_picks_up_entities_added_between_calls(compile_calls: list[int]) -> None:
    amap = _overlapping_map()
    masker = pii_gate.KnownEntityMasker(amap)

    assert masker.mask("Plain text with Acme Corp.") == "Plain text with [ORGANIZATION_2]."
    placeholder = amap.add_entity("Zed Q", "PERSON_NAME")
    assert masker.mask("Zed Q and Acme Corp.") == f"{placeholder} and [ORGANIZATION_2]."

    assert len(compile_calls) == 2
