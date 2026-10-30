"""One placeholder space out of several anonymization maps.

Every per-map placeholder moves as a unit to exactly one global placeholder;
two maps' placeholders share a global only when nothing else can claim it,
and two placeholders one map keeps apart never share one.
"""

from __future__ import annotations

import copy
import gc
import random
import re
import time
from typing import Callable, Dict, List, Sequence, Set, Tuple

import pytest
from septum_core.anonymization_map import AnonymizationMap
from septum_core.placeholder_space import (
    QUERY_MAP_KEY,
    map_placeholders,
    settle_strays,
    unify_placeholder_spaces,
)
from septum_core.unmasker import Unmasker

P1, P2, P3, P4, P5 = (f"[PERSON_NAME_{n}]" for n in range(1, 6))
_PLACEHOLDER = re.compile(r"\[[^\[\]]*\]")
Y = "Y" + chr(0x131) + "lmaz"
R = "R" + chr(0x131) + "za"

Maps = List[Tuple[int, AnonymizationMap]]


def _doc(key: int, language: str, *names: str, entity_type: str = "PERSON_NAME") -> AnonymizationMap:
    """A per-document map built the way ingestion builds it."""
    anon_map = AnonymizationMap(document_id=key, language=language)
    for name in names:
        anon_map.add_entity(name, entity_type)
    return anon_map


def _rename(text: str, remap: Dict[str, str]) -> str:
    return _PLACEHOLDER.sub(lambda m: remap.get(m.group(0), m.group(0)), text)


def _restore(text: str, remap: Dict[str, str], unified: AnonymizationMap) -> str:
    """Rename ``text`` into the global space, then unmask it."""
    return Unmasker().unmask(_rename(text, remap), unified)


def _with_query(query: AnonymizationMap, maps: Maps) -> Maps:
    return [(QUERY_MAP_KEY, query), *maps]


def _same_first_name() -> Maps:
    return [(1, _doc(1, "tr", "Ali Yılmaz", "Ali")), (2, _doc(2, "tr", "Ali Kaya", "Ali"))]


def _shared_surname(first: int = 1, second: int = 2) -> Maps:
    return [
        (first, _doc(first, "en", "John Smith", "Jane Smith", "Smith")),
        (second, _doc(second, "en", "Smith", "John Smith")),
    ]


def _case_and_suffix_variants() -> Maps:
    return [
        (1, _doc(1, "tr", "Ahmet Kaya", "Ahmet Kaya’nın", "AHMET KAYA")),
        (2, _doc(2, "tr", "Ahmet Kaya")),
    ]


def _same_full_name() -> Maps:
    return [(1, _doc(1, "tr", "Ali Yılmaz")), (2, _doc(2, "tr", "Ali Yılmaz"))]


def _legacy_two_people() -> Maps:
    legacy = AnonymizationMap(document_id=2, language="tr")
    legacy.entity_map = {"Ali Yılmaz": P1, "Veli Kaya": P1}
    return [(1, _doc(1, "tr", "Ali Yılmaz", "Veli Kaya")), (2, legacy)]


def _bare_names() -> Maps:
    return [
        (1, _doc(1, "tr", "Ali Yılmaz")),
        (2, _doc(2, "tr", "Ali")),
        (3, _doc(3, "tr", "Ali")),
    ]


def _query_copy() -> Maps:
    query = copy.deepcopy(_shared_surname()[0][1])
    query.add_entity("Mehmet Öz", "PERSON_NAME")
    return _with_query(query, _shared_surname())


def _non_person_values() -> Maps:
    a = _doc(1, "tr", "a@x.com", entity_type="EMAIL_ADDRESS")
    a.add_entity("+90 555 111 2233", "PHONE_NUMBER")
    a.add_entity("Antalya", "LOCATION")
    b = _doc(2, "tr", "a@x.com", entity_type="EMAIL_ADDRESS")
    b.add_entity("+90 555 999 8877", "PHONE_NUMBER")
    b.add_entity("Antalya", "ORGANIZATION_NAME")
    c = _doc(3, "tr", "A@X.com", entity_type="EMAIL_ADDRESS")
    return [(1, a), (2, b), (3, c)]


def _swapped_organizations() -> Maps:
    a = _doc(1, "en", "Alpha Holdings", "Beta Partners", entity_type="ORGANIZATION_NAME")
    b = _doc(2, "en", "Beta Partners", "Alpha Holdings", entity_type="ORGANIZATION_NAME")
    return [(1, a), (2, b)]


def _token_only() -> Maps:
    a = _doc(1, "en", "Ali Yılmaz")
    a.token_to_placeholder["zedd"] = "[PERSON_NAME_7]"
    return [(1, a)]


def _query_bare_name() -> Maps:
    return _with_query(_doc(0, "tr", "Ali"), [(1, _doc(1, "tr", "Ali Yılmaz"))])


def _query_bare_name_two_people() -> Maps:
    return _with_query(_doc(0, "tr", "Ali"), _same_first_name())


def _part_of_a_name() -> Maps:
    return [(1, _doc(1, "en", "John Smith")), (2, _doc(2, "en", "John Michael Smith"))]


def _two_entity_types() -> Maps:
    return [
        (1, _doc(1, "en", "John Smith")),
        (2, _doc(2, "en", "John Smith", entity_type="FIRST_NAME")),
    ]


def _seed_bare_surname() -> Maps:
    """Lead document knows only "Kaya"; another document knows "Ali Kaya"."""
    lead = _doc(1, "en", "Kaya")
    query = copy.deepcopy(lead)
    query.add_entity("Kaya", "PERSON_NAME")
    return _with_query(query, [(1, lead), (2, _doc(2, "en", "Ali Kaya"))])


def _seed_bare_names() -> Maps:
    """Lead document knows "Jane" and "Demir" apart; another knows "Jane Demir"."""
    lead = _doc(2, "en", "Jane", "Demir")
    query = copy.deepcopy(lead)
    query.add_entity("Jane", "PERSON_NAME")
    query.add_entity("Demir", "PERSON_NAME")
    return _with_query(query, [(1, _doc(1, "en", "Jane Demir")), (2, lead)])


def _seed_joined_surname() -> Maps:
    """Lead document holds "Demir-Kaya" and "Demir" under one placeholder."""
    lead = _doc(1, "en", "Demir-Kaya", "Demir")
    return _with_query(copy.deepcopy(lead), [(1, lead), (2, _doc(2, "en", "Demir"))])


def _bridge() -> Maps:
    """One map says "Ali Yılmaz" is "Ali Rıza Yılmaz"; another keeps them apart."""
    a = _doc(1, "tr", f"Ali {R} {Y}", f"Ali {Y}")
    x = _doc(2, "tr", f"Ali {Y}", f"{R} {Y}", f"Ali {R} {Y}")
    return [(1, a), (2, x)]


def _fuller_elsewhere() -> Maps:
    return [(1, _doc(1, "tr", f"Ali {Y} Kaya", f"Ali {Y}")), (2, _doc(2, "tr", f"Ali {Y}"))]


def _bare_under_other_type() -> Maps:
    a = _doc(1, "tr", "Ali", entity_type="ORGANIZATION_NAME")
    return [(1, a), (2, _doc(2, "tr", "Ali")), (3, _doc(3, "tr", "Ali"))]


def _custom_types() -> Maps:
    a = _doc(1, "en", "PX-4471-ZQ", entity_type="MRN2")
    a.add_entity("Codename Bluebird", "Project code")
    b = _doc(2, "en", "QY-1234-AB", entity_type="MRN2")
    b.add_entity("Codename Bluebird", "Project code")
    legacy = AnonymizationMap(document_id=3, language="en")
    legacy.entity_map = {"Old Value": "[LEGACY]"}
    return [(1, a), (2, b), (3, legacy)]


def _pronoun() -> Maps:
    a = _doc(1, "en", "Jane Roe")
    a.token_to_placeholder["she"] = P1
    return [(1, a), (2, _doc(2, "en", "John Poe"))]


_CASES: Dict[str, Callable[[], Maps]] = {
    "same_first_name": _same_first_name, "shared_surname": _shared_surname, "case_and_suffix_variants": _case_and_suffix_variants, "same_full_name": _same_full_name, "legacy_two_people": _legacy_two_people,
    "bare_names": _bare_names, "query_copy": _query_copy, "non_person_values": _non_person_values, "swapped_organizations": _swapped_organizations, "token_only": _token_only,
    "query_bare_name": _query_bare_name, "query_bare_name_two_people": _query_bare_name_two_people, "part_of_a_name": _part_of_a_name, "two_entity_types": _two_entity_types,
    "seed_bare_surname": _seed_bare_surname, "seed_bare_names": _seed_bare_names,
    "seed_joined_surname": _seed_joined_surname,
    "bridge": _bridge, "fuller_elsewhere": _fuller_elsewhere,
    "bare_under_other_type": _bare_under_other_type, "custom_types": _custom_types,
    "pronoun": _pronoun,
}


def test_same_first_name_in_two_documents_stays_two_people() -> None:
    remaps, unified = unify_placeholder_spaces(_same_first_name())

    assert remaps == {1: {P1: P1}, 2: {P1: P2}}
    assert unified.token_to_placeholder["ali"] == P3
    assert unified.entity_map["Ali"] == P3
    assert unified.placeholder_lookup == {P1: "Ali Yılmaz", P2: "Ali Kaya", P3: "Ali"}
    assert _restore("[PERSON_NAME_1] signed", remaps[1], unified) == "Ali Yılmaz signed"
    assert _restore("[PERSON_NAME_1] paid", remaps[2], unified) == "Ali Kaya paid"
    assert Unmasker().unmask(unified.apply_blocklist("Ali"), unified) == "Ali"


@pytest.mark.parametrize("ids", [(1, 2), (2, 1)])
def test_shared_surname_keeps_each_document_s_own_person(ids: Tuple[int, int]) -> None:
    maps = _shared_surname(*ids)
    remaps, unified = unify_placeholder_spaces(maps)
    a_key, b_key = ids

    assert remaps[a_key] == {P1: P1, P2: P2, P3: P3}
    assert remaps[b_key] == {P1: P1}
    assert unified.token_to_placeholder["smith"] == P3
    assert unified.entity_map["Smith"] == P3
    assert unified.placeholder_lookup == {P1: "John Smith", P2: "Jane Smith", P3: "Smith"}
    assert _restore("[PERSON_NAME_1] called", remaps[b_key], unified) == "John Smith called"


def test_suffixed_and_upper_case_forms_are_one_person() -> None:
    remaps, unified = unify_placeholder_spaces(_case_and_suffix_variants())

    assert remaps == {1: {P1: P1}, 2: {P1: P1}}
    assert unified.placeholder_lookup == {P1: "Ahmet Kaya"}
    for key in (1, 2):
        assert _rename(f"{P1} signed", remaps[key]) == f"{P1} signed"
        assert _restore(f"{P1} signed", remaps[key], unified) == "Ahmet Kaya signed"


def test_same_full_name_in_two_documents_is_one_global() -> None:
    remaps, unified = unify_placeholder_spaces(_same_full_name())

    assert remaps == {1: {P1: P1}, 2: {P1: P1}}
    assert unified.placeholder_lookup == {P1: "Ali Yılmaz"}
    assert _restore(f"{P1} paid", remaps[2], unified) == "Ali Yılmaz paid"


def test_a_legacy_placeholder_holding_two_people_merges_with_neither() -> None:
    maps = _legacy_two_people()
    remaps, unified = unify_placeholder_spaces(maps)

    assert remaps[1] == {P1: P1, P2: P2}
    assert remaps[2] == {P1: P3}
    assert _rename(f"{P1} left", remaps[2]) == f"{P3} left"
    assert _restore(f"{P1} left", remaps[2], unified) == Unmasker().unmask(f"{P1} left", maps[1][1])
    assert _restore(f"{P1} and {P2}", remaps[1], unified) == "Ali Yılmaz and Veli Kaya"


def test_bare_name_joins_another_bare_name_never_a_full_name() -> None:
    remaps, unified = unify_placeholder_spaces(_bare_names())

    assert remaps == {1: {P1: P1}, 2: {P1: P2}, 3: {P1: P2}}
    assert unified.placeholder_lookup == {P1: "Ali Yılmaz", P2: "Ali"}
    assert unified.token_to_placeholder["ali"] == P2
    assert _rename(f"{P1} came", remaps[3]) == f"{P2} came"
    assert _restore(f"{P1} came", remaps[3], unified) == "Ali came"


def test_query_copy_of_a_document_map_shares_every_global() -> None:
    remaps, unified = unify_placeholder_spaces(_query_copy())

    assert remaps[QUERY_MAP_KEY] == {P1: P1, P2: P2, P3: P3, P4: P4}
    assert remaps[1] == {P1: P1, P2: P2, P3: P3}
    assert remaps[2] == {P1: P1}
    assert _restore("[PERSON_NAME_3]", remaps[QUERY_MAP_KEY], unified) == "Smith"
    assert unified.placeholder_lookup[P4] == "Mehmet Öz"


def test_query_bare_name_joins_the_one_document_person_owning_it() -> None:
    remaps, unified = unify_placeholder_spaces(_query_bare_name())

    assert remaps == {QUERY_MAP_KEY: {P1: P1}, 1: {P1: P1}}
    assert unified.placeholder_lookup[P1] == "Ali Yılmaz"


def test_query_bare_name_stays_apart_when_two_document_people_own_it() -> None:
    remaps, unified = unify_placeholder_spaces(_query_bare_name_two_people())

    assert remaps[QUERY_MAP_KEY] == {P1: P3}
    assert unified.placeholder_lookup[P3] == "Ali"


def test_query_copy_of_a_bare_surname_follows_its_lead_document() -> None:
    remaps, unified = unify_placeholder_spaces(_seed_bare_surname())

    assert remaps[QUERY_MAP_KEY][P1] == remaps[1][P1]
    assert remaps[QUERY_MAP_KEY][P1] != remaps[2][P1]
    assert _restore(f"{P1} signed", remaps[QUERY_MAP_KEY], unified) == "Kaya signed"
    assert _restore(f"{P1} signed", remaps[2], unified) == "Ali Kaya signed"


def test_query_copy_of_bare_names_never_collapses_two_people() -> None:
    remaps, unified = unify_placeholder_spaces(_seed_bare_names())
    query = remaps[QUERY_MAP_KEY]

    assert query == {P1: remaps[2][P1], P2: remaps[2][P2]}
    assert query[P1] != query[P2]
    assert _restore(f"Did {P1} pay {P2}?", query, unified) == "Did Jane pay Demir?"
    assert _restore(f"{P1} signed", remaps[1], unified) == "Jane Demir signed"


def test_query_copy_of_a_placeholder_with_several_bare_names_follows_its_seed() -> None:
    maps = _seed_joined_surname()
    assert len(set(maps[1][1].entity_map.values())) == 1

    remaps, unified = unify_placeholder_spaces(maps)

    assert remaps[QUERY_MAP_KEY][P1] == remaps[1][P1]
    assert remaps[2][P1] != remaps[1][P1]
    assert _restore(f"{P1} signed", remaps[2], unified) == "Demir signed"


def test_non_person_values_merge_only_when_equal_and_of_one_type() -> None:
    remaps, unified = unify_placeholder_spaces(_non_person_values())
    email, phone_a, location = "[EMAIL_ADDRESS_1]", "[PHONE_NUMBER_1]", "[LOCATION_1]"

    assert remaps[1] == {email: email, phone_a: phone_a, location: location}
    assert remaps[2] == {
        email: email, phone_a: "[PHONE_NUMBER_2]", "[ORGANIZATION_NAME_1]": "[ORGANIZATION_NAME_1]"
    }
    assert remaps[3] == {email: email}
    assert _restore(f"mail {email}", remaps[3], unified) == "mail a@x.com"
    assert _restore(f"call {phone_a}", remaps[2], unified) == "call +90 555 999 8877"
    assert _restore("[ORGANIZATION_NAME_1] in [LOCATION_1]", remaps[2], unified) == "Antalya in Antalya"


def test_same_number_for_different_organizations_is_renamed_without_chaining() -> None:
    remaps, unified = unify_placeholder_spaces(_swapped_organizations())
    org1, org2 = "[ORGANIZATION_NAME_1]", "[ORGANIZATION_NAME_2]"

    assert remaps == {1: {org1: org1, org2: org2}, 2: {org1: org2, org2: org1}}
    text = f"{org1} sued {org2}"
    assert _restore(text, remaps[2], unified) == "Beta Partners sued Alpha Holdings"


def test_token_only_placeholder_gets_a_global_that_restores_to_nothing() -> None:
    remaps, unified = unify_placeholder_spaces(_token_only())

    assert remaps == {1: {P1: P1, "[PERSON_NAME_7]": P2}}
    assert unified.token_to_placeholder["zedd"] == P2
    assert P2 not in unified.placeholder_lookup
    assert Unmasker().unmask(f"{P2} left", unified) == f"{P2} left"


def test_a_name_that_is_part_of_another_never_merges() -> None:
    remaps, unified = unify_placeholder_spaces(_part_of_a_name())

    assert remaps == {1: {P1: P1}, 2: {P1: P2}}
    assert _restore(f"{P1} won", remaps[1], unified) == "John Smith won"
    assert _restore(f"{P1} won", remaps[2], unified) == "John Michael Smith won"


def test_same_name_of_two_entity_types_never_merges() -> None:
    remaps, unified = unify_placeholder_spaces(_two_entity_types())

    assert remaps == {1: {P1: P1}, 2: {"[FIRST_NAME_1]": "[FIRST_NAME_1]"}}
    assert _restore("[FIRST_NAME_1] won", remaps[2], unified) == "John Smith won"
    assert _restore(f"{P1} won", remaps[1], unified) == "John Smith won"


@pytest.mark.parametrize("ids", [(1, 2), (2, 1)])
def test_names_one_map_keeps_apart_never_bridge_through_another(ids: Tuple[int, int]) -> None:
    (_, a), (_, x) = _bridge()
    remaps, unified = unify_placeholder_spaces([(ids[0], a), (ids[1], x)])
    x_remap = remaps[ids[1]]

    assert len(set(x_remap.values())) == len(x_remap)
    assert _restore(f"{P1} paid", x_remap, unified) == f"Ali {Y} paid"
    assert _restore(f"{P4} paid", x_remap, unified) == f"Ali {R} {Y} paid"
    assert _restore(f"{P1} paid", remaps[ids[0]], unified) == f"Ali {R} {Y} paid"


def test_a_name_never_restores_as_a_fuller_name_from_another_document() -> None:
    remaps, unified = unify_placeholder_spaces(_fuller_elsewhere())

    assert remaps[2] == {P1: P2}
    assert _restore(f"{P1} paid", remaps[2], unified) == f"Ali {Y} paid"


def test_bare_name_held_under_another_type_still_has_one_person_global() -> None:
    remaps, unified = unify_placeholder_spaces(_bare_under_other_type())

    assert remaps[2] == remaps[3] == {P1: P1}
    assert _restore(f"{P1} came", remaps[3], unified) == "Ali came"


def test_custom_entity_types_are_carried_and_restored() -> None:
    remaps, unified = unify_placeholder_spaces(_custom_types())

    assert remaps[1] == {"[MRN2_1]": "[MRN2_1]", "[Project code_1]": "[Project code_1]"}
    assert remaps[2] == {"[MRN2_1]": "[MRN2_2]", "[Project code_1]": "[Project code_1]"}
    assert remaps[3] == {"[LEGACY]": "[LEGACY]"}
    assert {"PX-4471-ZQ", "QY-1234-AB", "Codename Bluebird", "Old Value"} <= set(unified.entity_map)
    text = "[MRN2_1] of [Project code_1]"
    assert _restore(text, remaps[2], unified) == "QY-1234-AB of Codename Bluebird"
    assert _restore("[LEGACY]", remaps[3], unified) == "Old Value"


def test_token_key_one_map_lacks_names_nobody() -> None:
    remaps, unified = unify_placeholder_spaces(_pronoun())

    neutral = unified.token_to_placeholder["she"]
    assert neutral not in {remaps[1][P1], remaps[2][P1]}
    assert unified.placeholder_lookup[neutral] == "she"
    retried = unified.apply_blocklist("Then she paid.")
    assert Unmasker().unmask(retried, unified) == "Then she paid."


def test_token_key_every_map_agrees_on_keeps_its_global() -> None:
    a = _doc(1, "en", "Jane Roe")
    a.token_to_placeholder["she"] = P1
    b = _doc(2, "en", "Jane Roe")
    b.token_to_placeholder["she"] = P1

    _, unified = unify_placeholder_spaces([(1, a), (2, b)])

    assert unified.token_to_placeholder["she"] == P1


def _token_order_maps(order: Sequence[Tuple[str, str]], other: Sequence[Tuple[str, str]]) -> Maps:
    maps: Maps = []
    for key, tokens in ((1, order), (2, other)):
        built = _doc(key, "en", "Ayse Demir", "Jane Roe")
        table = dict(built.token_to_placeholder)
        table.update(tokens)
        maps.append((key, AnonymizationMap(
            document_id=key, language="en", entity_map=dict(built.entity_map),
            token_to_placeholder=table, token_counter=dict(built.token_counter),
        )))
    return maps


def test_neutral_numbering_does_not_follow_token_insertion_order() -> None:
    first = _token_order_maps([("she", P1), ("he", P2)], [("she", P2), ("he", P1)])
    second = _token_order_maps([("he", P2), ("she", P1)], [("he", P1), ("she", P2)])

    _, a = unify_placeholder_spaces(first)
    _, b = unify_placeholder_spaces(second)

    assert a.token_to_placeholder == b.token_to_placeholder
    assert a.placeholder_lookup == b.placeholder_lookup


def test_stray_placeholder_is_kept_when_unused_and_never_handed_out() -> None:
    remaps, unified = unify_placeholder_spaces(_same_first_name())
    remap, known = dict(remaps[2]), {P1}

    settle_strays("[PERSON_NAME_9] and [PERSON_NAME_9]", remap, known, unified)

    assert "[PERSON_NAME_9]" in known and "[PERSON_NAME_9]" not in remap
    assert unified.add_entity("Ayşe Demir", "PERSON_NAME") not in {"[PERSON_NAME_9]", P1, P2, P3}
    assert Unmasker().unmask("[PERSON_NAME_9]", unified) == "[PERSON_NAME_9]"


def test_stray_placeholder_that_names_a_global_is_renamed_to_a_dead_one() -> None:
    remaps, unified = unify_placeholder_spaces(_same_first_name())
    remap, known = dict(remaps[2]), map_placeholders(_same_first_name()[1][1])

    settle_strays(f"{P3} met {P2}", remap, known, unified)
    settle_strays(f"{P3} again", remap, known, unified)

    dead = {remap[P3], remap[P2]}
    assert len(dead) == 2 and not dead & {P1, P2, P3}
    assert Unmasker().unmask(_rename(f"{P3} met {P2}", remap), unified) == _rename(f"{P3} met {P2}", remap)


def _shuffled(anon_map: AnonymizationMap, rng: random.Random) -> AnonymizationMap:
    """The same map with its entries and tokens inserted in another order."""
    entities = list(anon_map.entity_map.items())
    tokens = list(anon_map.token_to_placeholder.items())
    rng.shuffle(entities)
    rng.shuffle(tokens)
    return AnonymizationMap(
        document_id=anon_map.document_id, language=anon_map.language,
        entity_map=dict(entities), blocklist=set(anon_map.blocklist),
        token_to_placeholder=dict(tokens), token_counter=dict(anon_map.token_counter),
    )


def _orders(maps: Maps) -> List[Tuple[List[str], List[str]]]:
    return [(list(m.entity_map), list(m.token_to_placeholder)) for _, m in maps]


def _assert_same_result(maps: Maps, expected_remaps, expected: AnonymizationMap) -> None:
    remaps, unified = unify_placeholder_spaces(maps)
    assert remaps == expected_remaps
    assert list(unified.entity_map.items()) == list(expected.entity_map.items())
    assert unified.token_to_placeholder == expected.token_to_placeholder
    assert unified.placeholder_lookup == expected.placeholder_lookup


@pytest.mark.parametrize("case", sorted(_CASES))
def test_result_does_not_depend_on_input_order(case: str) -> None:
    expected_remaps, expected = unify_placeholder_spaces(_CASES[case]())
    original = _orders(_CASES[case]())
    reorderable = any(len(entities) > 1 or len(tokens) > 1 for entities, tokens in original)
    changed = False
    for seed in range(8):
        rng = random.Random(seed)
        maps = [(key, _shuffled(m, rng)) for key, m in _CASES[case]()]
        rng.shuffle(maps)
        changed |= _orders(sorted(maps, key=lambda item: item[0])) != original
        _assert_same_result(maps, expected_remaps, expected)
    assert changed or not reorderable


def _public_state(maps: Maps) -> list:
    return [
        (key, dict(m.entity_map), list(m.entity_map), dict(m.token_to_placeholder),
         set(m.blocklist), dict(m.token_counter), dict(m.placeholder_lookup))
        for key, m in maps
    ]


@pytest.mark.parametrize("case", sorted(_CASES))
def test_input_maps_are_left_unchanged(case: str) -> None:
    maps = _CASES[case]()
    before = _public_state(maps)

    unify_placeholder_spaces(maps)

    assert _public_state(maps) == before


def _full_keys(anon_map: AnonymizationMap, placeholder: str) -> frozenset:
    return frozenset(key for key in anon_map.person_group_keys(placeholder) if len(key) >= 2)


def _assert_full_names_never_mix(maps: Sequence[Tuple[int, AnonymizationMap]], remaps) -> None:
    """Placeholders with full names that share a global have exactly the same full names."""
    by_global: Dict[str, Set[frozenset]] = {}
    for key, anon_map in maps:
        for placeholder, chosen in remaps[key].items():
            keys = _full_keys(anon_map, placeholder)
            if keys:
                by_global.setdefault(chosen, set()).add(keys)
    assert all(len(owners) == 1 for owners in by_global.values())


def _words(text: str) -> Set[str]:
    return set(re.findall(r"\w+", text.casefold()))


def _assert_restores_own_original(key: int, anon_map: AnonymizationMap, remap, unified) -> None:
    """A global restores to what the document's own map restores its placeholder to.

    The one exception is the query's bare name joined to the one person
    owning it, which restores to that person's fuller name.
    """
    for placeholder in set(anon_map.entity_map.values()):
        own = Unmasker().unmask(placeholder, anon_map)
        got = Unmasker().unmask(remap[placeholder], unified)
        if got.casefold() == own.casefold():
            continue
        assert key == QUERY_MAP_KEY and _words(own) < _words(got), (placeholder, own, got)


@pytest.mark.parametrize("case", sorted(_CASES))
def test_invariants(case: str) -> None:
    maps = _CASES[case]()
    remaps, unified = unify_placeholder_spaces(maps)

    orphans = set()
    for key, anon_map in maps:
        remap = remaps[key]
        assert set(remap) == map_placeholders(anon_map)
        assert len(set(remap.values())) == len(remap), "one map's placeholders kept apart"
        orphans |= {remap[p] for p in set(anon_map.token_to_placeholder.values()) - set(anon_map.entity_map.values())}
        _assert_restores_own_original(key, anon_map, remap, unified)
    for placeholder in map_placeholders(unified):
        assert placeholder in unified.placeholder_lookup or placeholder in orphans
    _assert_full_names_never_mix(maps, remaps)


def _word(number: int) -> str:
    """A letters-only name word for ``number``."""
    letters = "bcdfghklmnprstvz"
    out = ""
    while True:
        out += letters[number % len(letters)]
        number //= len(letters)
        if not number:
            return "Na" + out + "o"


def _corpus(map_count: int, persons: int = 500, surnames: int = 30) -> Maps:
    maps: Maps = []
    for key in range(map_count):
        anon_map = AnonymizationMap(document_id=key, language="en")
        for index in range(persons):
            given = _word(key * 37 + index)
            anon_map.add_entity(f"{given} {_word(10_000 + index % surnames)}", "PERSON_NAME")
        maps.append((key, anon_map))
    return maps


def _best_time(maps: Maps) -> float:
    """Best of three runs with the collector on.

    The input corpus is frozen out of the collector first, so a full
    collection during a run scans what the run allocated, not the test's
    own fixture.
    """
    best = float("inf")
    gc.collect()
    gc.freeze()
    try:
        for _ in range(3):
            started = time.perf_counter()
            unify_placeholder_spaces(maps)
            best = min(best, time.perf_counter() - started)
    finally:
        gc.unfreeze()
    return best


def test_scales_linearly() -> None:
    small, large = _corpus(40), _corpus(80)

    small_time, large_time = _best_time(small), _best_time(large)

    assert small_time < 5.0
    assert large_time <= 3.0 * small_time


def _custom_and_person() -> Tuple[Dict[int, Dict[str, str]], AnonymizationMap, AnonymizationMap]:
    a = _doc(1, "en", "PX-1111", entity_type="MRN2")
    a.add_entity("Codename Bluebird", "Project code")
    b = _doc(2, "en", "PX-2222", entity_type="MRN2")
    b.add_entity("Codename Redwing", "Project code")
    c = _doc(3, "en", "Ali Kaya")
    remaps, unified = unify_placeholder_spaces(_with_query(AnonymizationMap(document_id=0), [(1, a), (2, b), (3, c)]))
    return remaps, unified, c


@pytest.mark.parametrize("stray", ["[MRN2_2]", "[Project code_2]", "[PERSON_1]"])
def test_stray_of_a_custom_type_or_short_alias_never_restores_another_document_s_value(stray: str) -> None:
    remaps, unified, c = _custom_and_person()
    remap = dict(remaps[3])
    text = f"doc C mentions {stray}"

    settle_strays(text, remap, map_placeholders(c), unified)

    renamed = _rename(text, remap)
    assert stray not in renamed
    assert Unmasker().unmask(renamed, unified) == renamed


def test_kept_short_alias_stray_reserves_its_full_form() -> None:
    remaps, unified, c = _custom_and_person()
    remap, known = dict(remaps[3]), map_placeholders(c)

    settle_strays("see [PERSON_2]", remap, known, unified)

    assert "[PERSON_2]" in known
    assert unified.add_entity("Zeynep Ak", "PERSON_NAME") != "[PERSON_NAME_2]"


def test_shapeless_placeholder_two_maps_share_restores_each_map_s_own_value() -> None:
    first = AnonymizationMap(document_id=1, language="en")
    first.entity_map = {"Old A": "[LEGACY]"}
    second = AnonymizationMap(document_id=2, language="en")
    second.entity_map = {"Old B": "[LEGACY]"}

    remaps, unified = unify_placeholder_spaces([(1, first), (2, second)])

    assert remaps[1]["[LEGACY]"] != remaps[2]["[LEGACY]"]
    assert _restore("[LEGACY]", remaps[1], unified) == "Old A"
    assert _restore("[LEGACY]", remaps[2], unified) == "Old B"


def test_renamed_short_alias_stray_never_aliases_a_later_entity() -> None:
    remaps, unified, c = _custom_and_person()
    remap = dict(remaps[3])
    settle_strays("see [PERSON_1]", remap, map_placeholders(c), unified)
    renamed = remap["[PERSON_1]"]
    full_form = renamed.replace("[PERSON_", "[PERSON_NAME_")

    minted = {unified.add_entity(f"Person{n} Example", "PERSON_NAME") for n in range(12)}

    assert full_form not in minted
    assert Unmasker().unmask(renamed, unified) == renamed
