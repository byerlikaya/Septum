"""Shared name tokens, joined-name coreference and map persistence.

A name part shared by two people is masked under a neutral placeholder that
restores to the part alone; a joined name ("Jean-Pierre") is one name, not a
set of names; and a map keeps all of this across a save and load.
"""

from __future__ import annotations

import json
import re

import pytest
from septum_core.anonymization_map import AnonymizationMap
from septum_core.unmasker import Unmasker

# Letter runs of a name, the way a reader sees its words.
_WORDS = re.compile(r"[^\W\d_]{3,}")


def _map(language: str = "en") -> AnonymizationMap:
    return AnonymizationMap(document_id=1, language=language)


def _restore(text: str, anon_map: AnonymizationMap) -> str:
    return Unmasker().unmask(text, anon_map)


def _reload(anon_map: AnonymizationMap) -> AnonymizationMap:
    """Save and load ``anon_map`` in the document store's payload shape."""
    payload = json.loads(
        json.dumps(
            {
                "entity_map": anon_map.entity_map,
                "language": anon_map.language,
                "token_to_placeholder": anon_map.token_to_placeholder,
                "token_counter": anon_map.token_counter,
            },
            ensure_ascii=False,
        )
    )
    return AnonymizationMap(
        document_id=anon_map.document_id,
        language=payload["language"],
        entity_map=payload["entity_map"],
        token_to_placeholder=payload["token_to_placeholder"],
        token_counter=payload["token_counter"],
    )


# --------------------------------------------------------------------------
# A bare form moves to a neutral placeholder once it is shared
# --------------------------------------------------------------------------


def test_bare_form_moves_to_neutral_placeholder_when_shared() -> None:
    anon_map = _map()
    john = anon_map.add_entity("John Smith", "PERSON_NAME")
    assert anon_map.add_entity("Smith", "PERSON_NAME") == john
    earlier = anon_map.apply_blocklist("Smith called. John Smith left.")

    jane = anon_map.add_entity("Jane Smith", "PERSON_NAME")
    neutral = anon_map.entity_map["Smith"]

    assert neutral not in (john, jane)
    assert anon_map.token_to_placeholder["smith"] == neutral
    assert anon_map.add_entity("Smith", "PERSON_NAME") == neutral
    assert anon_map.apply_blocklist("Smith") == neutral
    assert _restore(neutral, anon_map) == "Smith"
    assert _restore(john, anon_map) == "John Smith"
    assert _restore(jane, anon_map) == "Jane Smith"
    assert _restore(earlier, anon_map) == "John Smith called. John Smith John Smith left."


def test_bare_form_seen_before_both_people_moves_too() -> None:
    anon_map = _map()
    first = anon_map.add_entity("Smith", "LAST_NAME")
    assert anon_map.add_entity("John Smith", "PERSON_NAME") == first

    jane = anon_map.add_entity("Jane Smith", "PERSON_NAME")
    neutral = anon_map.entity_map["Smith"]

    assert neutral not in (first, jane)
    assert _restore(first, anon_map) == "John Smith"
    assert _restore(neutral, anon_map) == "Smith"


@pytest.mark.parametrize(
    "names",
    [
        ("Ali Yılmaz", "Ali Kaya", "Ali"),
        ("Ali Kaya", "Ali Yılmaz", "Ali"),
        ("Ali", "Ali Yılmaz", "Ali Kaya"),
        ("Ali", "Ali Kaya", "Ali Yılmaz"),
        ("Ali Yılmaz", "Ali", "Ali Kaya"),
    ],
)
def test_registration_order_never_merges_the_two_people(names: tuple[str, str, str]) -> None:
    anon_map = _map()
    for name in names:
        anon_map.add_entity(name, "PERSON_NAME")

    yilmaz = anon_map.add_entity("Ali Yılmaz", "PERSON_NAME")
    kaya = anon_map.add_entity("Ali Kaya", "PERSON_NAME")
    bare = anon_map.add_entity("Ali", "PERSON_NAME")

    assert len({yilmaz, kaya, bare}) == 3
    assert anon_map.apply_blocklist("Ali") == bare
    assert _restore(f"{yilmaz}|{kaya}|{bare}", anon_map) == "Ali Yılmaz|Ali Kaya|Ali"


def test_a_placeholder_never_loses_its_last_original() -> None:
    anon_map = _map()
    jean = anon_map.add_entity("Jean", "FIRST_NAME")
    other = anon_map.add_entity("Jean-Pierre Dupont", "PERSON_NAME")

    assert other != jean
    assert anon_map.entity_map["Jean"] == jean
    assert anon_map.token_to_placeholder["jean"] == jean
    assert _restore(jean, anon_map) == "Jean"
    assert _restore(other, anon_map) == "Jean-Pierre Dupont"


def test_three_people_sharing_a_token_stay_distinct() -> None:
    anon_map = _map()
    people = ["Ali Yılmaz", "Ali Kaya", "Ali Demir"]
    placeholders = [anon_map.add_entity(name, "PERSON_NAME") for name in people]
    bare = anon_map.add_entity("Ali", "PERSON_NAME")

    assert len(set(placeholders) | {bare}) == 4
    for name, placeholder in zip(people, placeholders):
        assert _restore(placeholder, anon_map) == name
        assert anon_map.apply_blocklist(name.split()[1]) == placeholder
    assert anon_map.apply_blocklist("ali") == bare


def test_part_shared_by_a_hyphenated_and_a_plain_name() -> None:
    anon_map = _map()
    hyphenated = anon_map.add_entity("Jean-Pierre Dupont", "PERSON_NAME")
    plain = anon_map.add_entity("Jean Martin", "PERSON_NAME")
    bare = anon_map.apply_blocklist("Jean")

    assert plain != hyphenated
    assert bare not in (hyphenated, plain)
    assert anon_map.apply_blocklist("Pierre Martin") == f"{hyphenated} {plain}"
    assert _restore(bare, anon_map) == "Jean"


def test_upper_and_mixed_case_bare_forms_move_together() -> None:
    anon_map = _map()
    john = anon_map.add_entity("John Smith", "PERSON_NAME")
    assert anon_map.add_entity("SMITH", "PERSON_NAME") == john
    assert anon_map.add_entity("smith", "PERSON_NAME") == john

    anon_map.add_entity("Jane Smith", "PERSON_NAME")
    neutral = anon_map.entity_map["SMITH"]

    assert anon_map.entity_map["smith"] == neutral
    assert neutral != john
    assert _restore(john, anon_map) == "John Smith"
    assert _restore(neutral, anon_map) == "SMITH"


def test_moved_bare_form_survives_a_reload() -> None:
    anon_map = _map()
    john = anon_map.add_entity("John Smith", "PERSON_NAME")
    anon_map.add_entity("Smith", "PERSON_NAME")
    anon_map.add_entity("Jane Smith", "PERSON_NAME")
    neutral = anon_map.entity_map["Smith"]

    loaded = _reload(anon_map)

    assert loaded.add_entity("Smith", "PERSON_NAME") == neutral
    assert loaded.add_entity("John Smith", "PERSON_NAME") == john
    jim = loaded.add_entity("Jim Smith", "PERSON_NAME")
    assert jim not in (john, neutral)
    assert loaded.token_to_placeholder["smith"] == neutral
    assert _restore(f"{john} {neutral} {jim}", loaded) == "John Smith Smith Jim Smith"


def test_mirrored_lookup_resolves_the_neutral_placeholder() -> None:
    anon_map = _map()
    # Mirror mode, as the PII gate does it: each placeholder add_entity returns
    # is recorded with the original it was returned for.
    first = anon_map.add_entity("Smith", "PERSON_NAME")
    anon_map.placeholder_lookup[first] = "Smith"
    john = anon_map.add_entity("John Smith", "PERSON_NAME")
    assert john == first
    anon_map.add_entity("Jane Smith", "PERSON_NAME")
    neutral = anon_map.entity_map["Smith"]

    assert anon_map.placeholder_lookup[neutral] == "Smith"
    assert anon_map.placeholder_lookup[john] == "John Smith"
    assert _restore(f"{john} / {neutral}", anon_map) == "John Smith / Smith"


# --------------------------------------------------------------------------
# A joined name is one name for coreference
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("Jean-Pierre Dupont", "Jean Dupont"),
        ("Jean Dupont", "Jean-Pierre Dupont"),
        ("John Smith", "John Smith-Jones"),
        ("John Smith-Jones", "John Smith"),
        ("Ali Kaya", "Ali-Rıza Kaya"),
        ("Ali-Rıza Kaya", "Ali Kaya"),
        ("Mary O'Brien", "Mary Brien"),
        ("Mary Brien", "Mary O'Brien"),
        ("Anne-Marie Martin", "Marie Martin"),
        ("Anne-Marie Martin", "Anne Martin"),
        ("Marie Martin", "Anne-Marie Martin"),
    ],
)
def test_joined_and_plain_names_stay_separate(first: str, second: str) -> None:
    anon_map = _map()

    one = anon_map.add_entity(first, "PERSON_NAME")
    two = anon_map.add_entity(second, "PERSON_NAME")

    assert one != two
    assert _restore(f"{one} / {two}", anon_map) == f"{first} / {second}"
    shared = set(_WORDS.findall(first)) & set(_WORDS.findall(second))
    assert shared
    for word in shared:
        bare = anon_map.apply_blocklist(word)
        assert bare not in (one, two, word)
        assert _restore(bare, anon_map) == word


def test_three_names_around_a_hyphenated_given_name_stay_separate() -> None:
    anon_map = _map()
    names = ["Anne-Marie Martin", "Marie Martin", "Anne Martin"]

    placeholders = [anon_map.add_entity(name, "PERSON_NAME") for name in names]

    assert len(set(placeholders)) == 3
    for name, placeholder in zip(names, placeholders):
        assert _restore(placeholder, anon_map) == name
    for word in ("Martin", "Marie", "Anne"):
        bare = anon_map.apply_blocklist(word)
        assert bare not in placeholders
        assert _restore(bare, anon_map) == word


@pytest.mark.parametrize("order", [("Jean-Pierre Dupont", "Jean Pierre Dupont"), ("Jean Pierre Dupont", "Jean-Pierre Dupont")])
def test_spaced_and_hyphenated_forms_are_one_name(order: tuple[str, str]) -> None:
    anon_map = _map()

    first = anon_map.add_entity(order[0], "PERSON_NAME")

    assert anon_map.add_entity(order[1], "PERSON_NAME") == first


def test_single_bare_part_still_corefers_with_the_joined_name() -> None:
    anon_map = _map()
    placeholder = anon_map.add_entity("Jean-Pierre Dupont", "PERSON_NAME")

    assert anon_map.add_entity("Pierre", "FIRST_NAME") == placeholder
    assert anon_map.add_entity("Jean-Pierre", "FIRST_NAME") == placeholder


# --------------------------------------------------------------------------
# Suffix tails never become name keys
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["Kaya’NIN Demir", "Kaya’Nın Demir", "Kaya'NIN Demir"])
def test_suffix_tail_is_never_a_name_key(name: str) -> None:
    anon_map = _map()

    anon_map.add_entity(name, "PERSON_NAME")

    assert "nin" not in anon_map.token_to_placeholder
    assert "nın" not in anon_map.token_to_placeholder
    assert anon_map.apply_blocklist("Mehmet’nin evi") == "Mehmet’nin evi"


def test_suffix_tail_is_never_derived_on_load() -> None:
    anon_map = AnonymizationMap(
        document_id=1,
        entity_map={"Kaya’NIN": "[PERSON_NAME_1]"},
        token_to_placeholder={"kaya’nin": "[PERSON_NAME_1]"},
    )

    assert "nin" not in anon_map.token_to_placeholder
    assert "kaya" in anon_map.token_to_placeholder


# --------------------------------------------------------------------------
# Persistence and pronoun keys
# --------------------------------------------------------------------------


def test_new_map_round_trips_through_the_store_payload() -> None:
    anon_map = _map()
    for name in ("Jean-Pierre Dupont", "Jean Martin", "O'Brien", "Ali Yılmaz", "Ali", "Ali Kaya"):
        anon_map.add_entity(name, "PERSON_NAME")
    text = "Jean-Pierre, Jean, Pierre, Martin, O’Brien, Brien, Ali, Yılmaz and Kaya."

    loaded = _reload(anon_map)

    assert loaded.entity_map == anon_map.entity_map
    assert loaded.token_to_placeholder == anon_map.token_to_placeholder
    assert loaded.apply_blocklist(text) == anon_map.apply_blocklist(text)
    for name in ("Jean-Pierre Dupont", "Jean Martin", "Ali", "Ali Kaya", "Jean"):
        assert loaded.add_entity(name, "PERSON_NAME") == anon_map.add_entity(name, "PERSON_NAME")


def test_pronoun_keys_are_untouched_by_load() -> None:
    keys = {"he": "[PERSON_NAME_1]", "kendisi": "[PERSON_NAME_1]", "o kişi": "[PERSON_NAME_2]"}

    anon_map = AnonymizationMap(
        document_id=1,
        entity_map={"Ali Kaya": "[PERSON_NAME_1]", "Ayşe Demir": "[PERSON_NAME_2]"},
        token_to_placeholder=dict(keys),
    )

    assert anon_map.token_to_placeholder == keys
    assert anon_map.blocklist == set(keys)


# --------------------------------------------------------------------------
# All-caps apostrophe joins in coreference
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "order", [("ANNE’MARIE DUBOIS", "Anne Dubois"), ("Anne Dubois", "ANNE’MARIE DUBOIS")]
)
def test_all_caps_name_join_does_not_corefer_through_its_head(order: tuple[str, str]) -> None:
    anon_map = _map()

    first = anon_map.add_entity(order[0], "PERSON_NAME")
    second = anon_map.add_entity(order[1], "PERSON_NAME")

    assert first != second
    assert _restore(f"{first} / {second}", anon_map) == f"{order[0]} / {order[1]}"


@pytest.mark.parametrize(
    ("language", "caps"), [("tr", "ALİ KAYA’NIN"), ("en", "ALI KAYA’NIN"), ("en", "ALI KAYA'S")]
)
@pytest.mark.parametrize("caps_first", [True, False])
def test_all_caps_suffix_join_still_corefers(language: str, caps: str, caps_first: bool) -> None:
    anon_map = _map(language)
    order = (caps, "Ali Kaya") if caps_first else ("Ali Kaya", caps)
    text = f"{order[0]} and {order[1]}"

    first = anon_map.add_entity(order[0], "PERSON_NAME")
    second = anon_map.add_entity(order[1], "PERSON_NAME")
    masked = text.replace(order[0], first, 1).replace(order[1], second, 1)

    assert first == second
    assert _restore(masked, anon_map) == "Ali Kaya and Ali Kaya"
    assert _restore(first, anon_map) == "Ali Kaya"



# --------------------------------------------------------------------------
# Modifier apostrophe, moved suffix keys, index sync, word order, old maps
# --------------------------------------------------------------------------

_MODIFIER_APOSTROPHE = "ʼ"


@pytest.mark.parametrize("language", ["en", "tr"])
@pytest.mark.parametrize("name", ["OʼBrien", "Mary OʼBrien"])
def test_modifier_letter_apostrophe_name_is_masked(name: str, language: str) -> None:
    anon_map = _map(language)
    placeholder = anon_map.add_entity(name, "PERSON_NAME")

    masked = anon_map.apply_blocklist("Then OʼBrien and oʼbrien and O'Brien and Brien left.")

    assert "rien" not in masked
    assert masked.count(placeholder) == 4


def test_modifier_letter_apostrophe_key_of_an_old_map_is_masked() -> None:
    anon_map = AnonymizationMap(
        document_id=1,
        entity_map={"Mary OʼBrien": "[PERSON_NAME_1]"},
        token_to_placeholder={"mary": "[PERSON_NAME_1]", "oʼbrien": "[PERSON_NAME_1]"},
        token_counter={"PERSON_NAME": 1},
    )

    masked = anon_map.apply_blocklist("OʼBrien, O'Brien, Brien")

    assert masked == "[PERSON_NAME_1], [PERSON_NAME_1], [PERSON_NAME_1]"


def test_moved_bare_form_takes_its_suffix_keys_along() -> None:
    anon_map = _map()
    john = anon_map.add_entity("John Smith", "PERSON_NAME")
    assert anon_map.add_entity("SMITH’S", "PERSON_NAME") == john
    anon_map.add_entity("Jane Smith", "PERSON_NAME")
    neutral = anon_map.entity_map["SMITH’S"]

    masked = anon_map.apply_blocklist("SMITH'S car; Smith's car; SMITH’S car")

    assert neutral != john
    assert john not in masked
    assert masked.count(neutral) == 3


def test_moved_suffixed_bare_form_does_not_restore_to_the_first_person() -> None:
    anon_map = _map("tr")
    ali = anon_map.add_entity("Ali Kaya", "PERSON_NAME")
    anon_map.add_entity("Kaya", "PERSON_NAME")
    anon_map.add_entity("KAYA’NIN", "PERSON_NAME")
    anon_map.add_entity("Ayşe Kaya", "PERSON_NAME")

    masked = anon_map.apply_blocklist("Kaya geldi. KAYA’NIN evi.")

    assert ali not in masked
    assert "Ali" not in _restore(masked, anon_map)


def test_directly_filled_map_is_indexed_before_an_exact_match() -> None:
    anon_map = AnonymizationMap(document_id=0, language="en")
    anon_map.entity_map.update(
        {"John Smith": "[PERSON_NAME_1]", "Jane Smith": "[PERSON_NAME_2]", "Bob Ray": "[PERSON_NAME_3]"}
    )
    anon_map.token_to_placeholder.update(
        {"john": "[PERSON_NAME_1]", "smith": "[PERSON_NAME_2]", "jane": "[PERSON_NAME_2]",
         "bob": "[PERSON_NAME_3]", "ray": "[PERSON_NAME_3]"}
    )
    anon_map.token_counter = {"PERSON_NAME": 3}

    assert anon_map.add_entity("John Smith", "PERSON_NAME") == "[PERSON_NAME_1]"
    assert anon_map.add_entity("JOHN SMITH", "PERSON_NAME") == "[PERSON_NAME_1]"
    assert anon_map.add_entity("Bob", "PERSON_NAME") == "[PERSON_NAME_3]"


def test_neutral_moves_do_not_rebuild_the_index(monkeypatch: pytest.MonkeyPatch) -> None:
    # Deliberately coupled to the internals: it counts ``_PersonIndex`` builds
    # to pin that a neutral move updates the index in place. The behavioural
    # asserts below check the moves themselves.
    from septum_core import anonymization_map as module

    builds: list[int] = []
    original_init = module._PersonIndex.__init__

    def counting_init(self) -> None:
        builds.append(1)
        original_init(self)

    monkeypatch.setattr(module._PersonIndex, "__init__", counting_init)
    anon_map = _map()
    assert builds == [1], "control: building a map builds its index once"

    for index in range(30):
        surname = f"Surname{chr(97 + index % 26)}{index}"
        anon_map.add_entity(f"First{index} {surname}", "PERSON_NAME")
        anon_map.add_entity(surname, "PERSON_NAME")
        anon_map.add_entity(f"Other{index} {surname}", "PERSON_NAME")

    assert builds == [1]
    for index in (0, 29):
        surname = f"Surname{chr(97 + index % 26)}{index}"
        first = anon_map.entity_map[f"First{index} {surname}"]
        other = anon_map.entity_map[f"Other{index} {surname}"]
        neutral = anon_map.entity_map[surname]
        assert neutral not in (first, other)
        assert anon_map.apply_blocklist(surname) == neutral
        assert _restore(f"{first}|{other}|{neutral}", anon_map) == (
            f"First{index} {surname}|Other{index} {surname}|{surname}"
        )


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("Jean-Pierre Dupont", "Pierre Jean"),
        ("Pierre Jean", "Jean-Pierre Dupont"),
        ("Anne Lee'İsmail Ayşe", "İsmail Lee"),
        ("İsmail Lee", "Anne Lee'İsmail Ayşe"),
    ],
)
def test_spaced_form_in_another_order_is_another_name(first: str, second: str) -> None:
    anon_map = _map()

    one = anon_map.add_entity(first, "PERSON_NAME")
    two = anon_map.add_entity(second, "PERSON_NAME")

    assert one != two
    assert _restore(f"{one} / {two}", anon_map) == f"{first} / {second}"


def test_old_map_with_a_shared_surname_moves_it_to_a_neutral_placeholder() -> None:
    anon_map = AnonymizationMap(
        document_id=1,
        entity_map={"John Smith": "[PERSON_NAME_1]", "Smith": "[PERSON_NAME_1]", "Jane Smith": "[PERSON_NAME_2]"},
        token_to_placeholder={"john": "[PERSON_NAME_1]", "smith": "[PERSON_NAME_1]", "jane": "[PERSON_NAME_2]"},
        token_counter={"PERSON_NAME": 2},
    )

    neutral = anon_map.token_to_placeholder["smith"]

    assert neutral not in ("[PERSON_NAME_1]", "[PERSON_NAME_2]")
    assert anon_map.entity_map["Smith"] == neutral
    assert anon_map.apply_blocklist("Smith") == neutral
    assert _restore(f"[PERSON_NAME_1] / [PERSON_NAME_2] / {neutral}", anon_map) == (
        "John Smith / Jane Smith / Smith"
    )


def test_old_map_without_the_bare_form_gets_a_neutral_placeholder() -> None:
    anon_map = AnonymizationMap(
        document_id=1,
        entity_map={"John Smith": "[PERSON_NAME_1]", "Jane Smith": "[PERSON_NAME_2]"},
        token_to_placeholder={"john": "[PERSON_NAME_1]", "smith": "[PERSON_NAME_2]", "jane": "[PERSON_NAME_2]"},
        token_counter={"PERSON_NAME": 2},
    )

    neutral = anon_map.token_to_placeholder["smith"]

    assert neutral == "[PERSON_NAME_3]"
    assert _restore(neutral, anon_map) == "Smith"
    assert anon_map.token_counter["PERSON_NAME"] == 3


def test_new_map_is_unchanged_by_a_reload() -> None:
    anon_map = _map()
    for name in ("John Smith", "Smith", "Jane Smith", "Ali Yılmaz", "Ali Kaya"):
        anon_map.add_entity(name, "PERSON_NAME")

    loaded = _reload(anon_map)

    assert loaded.entity_map == anon_map.entity_map
    assert loaded.token_to_placeholder == anon_map.token_to_placeholder
    assert loaded.token_counter == anon_map.token_counter


# The rule is asymmetric on purpose. A lone part after the full joined name
# is that person ("Jean" after "Jean-Pierre Dupont", like "Ali" after "Ali
# Yılmaz"). A lone part seen first is a name of its own: the later joined
# name is a different name, so it gets its own placeholder rather than
# claiming the earlier "Jean" was Jean-Pierre.
@pytest.mark.parametrize(
    ("first", "second", "corefer", "first_restores_to"),
    [
        ("Jean-Pierre Dupont", "Jean", True, "Jean-Pierre Dupont"),
        ("Jean-Pierre Dupont", "Pierre", True, "Jean-Pierre Dupont"),
        ("Ali Yılmaz", "Ali", True, "Ali Yılmaz"),
        ("Jean", "Jean-Pierre Dupont", False, "Jean"),
        ("Pierre", "Jean-Pierre Dupont", False, "Pierre"),
    ],
)
def test_order_of_a_part_and_its_joined_name(
    first: str, second: str, corefer: bool, first_restores_to: str
) -> None:
    anon_map = _map()

    one = anon_map.add_entity(first, "PERSON_NAME")
    two = anon_map.add_entity(second, "PERSON_NAME")

    assert (one == two) is corefer
    assert _restore(one, anon_map) == first_restores_to
    if not corefer:
        assert _restore(two, anon_map) == second



# --------------------------------------------------------------------------
# Placeholder counters, large holders, lowercase name continuations
# --------------------------------------------------------------------------


@pytest.mark.parametrize("counter", [{}, {"PERSON_NAME": 1}])
def test_load_never_mints_a_placeholder_already_in_use(counter: dict) -> None:
    anon_map = AnonymizationMap(
        document_id=1,
        entity_map={"John Smith": "[PERSON_NAME_1]", "Jane Smith": "[PERSON_NAME_2]", "Smith": "[PERSON_NAME_2]"},
        token_to_placeholder={"john": "[PERSON_NAME_1]", "smith": "[PERSON_NAME_2]", "jane": "[PERSON_NAME_2]"},
        token_counter=dict(counter),
    )

    neutral = anon_map.entity_map["Smith"]

    assert neutral not in ("[PERSON_NAME_1]", "[PERSON_NAME_2]")
    assert _restore("[PERSON_NAME_1] / [PERSON_NAME_2] / " + neutral, anon_map) == (
        "John Smith / Jane Smith / Smith"
    )
    assert anon_map.add_entity("Bob Ray", "PERSON_NAME") not in (
        "[PERSON_NAME_1]", "[PERSON_NAME_2]", neutral,
    )


def test_minting_skips_placeholders_used_only_in_lookup_or_keys() -> None:
    anon_map = AnonymizationMap(document_id=0, language="en")
    anon_map.placeholder_lookup["[PERSON_NAME_1]"] = "Someone Else"
    anon_map.token_to_placeholder["kendisi"] = "[PERSON_NAME_2]"

    first = anon_map.add_entity("Ali Kaya", "PERSON_NAME")

    assert first == "[PERSON_NAME_3]"


def test_load_moves_bare_forms_off_a_large_holder() -> None:
    entity_map: dict[str, str] = {}
    keys: dict[str, str] = {"bigholder": "[PERSON_NAME_1]"}
    for index in range(40):
        first, other = f"Aname{chr(97 + index % 26)}{index}", f"Cname{index}"
        entity_map[f"{first} Bigholder"] = "[PERSON_NAME_1]"
        entity_map[first] = "[PERSON_NAME_1]"
        entity_map[f"{first} {other}"] = f"[PERSON_NAME_{index + 2}]"
        keys[first.lower()] = "[PERSON_NAME_1]"
        keys[other.lower()] = f"[PERSON_NAME_{index + 2}]"

    anon_map = AnonymizationMap(
        document_id=1, entity_map=entity_map, token_to_placeholder=keys, token_counter={"PERSON_NAME": 41}
    )

    for index in (0, 39):
        first = f"Aname{chr(97 + index % 26)}{index}"
        neutral = anon_map.entity_map[first]
        assert neutral not in ("[PERSON_NAME_1]", f"[PERSON_NAME_{index + 2}]")
        assert anon_map.token_to_placeholder[first.lower()] == neutral
    assert _restore("[PERSON_NAME_1]", anon_map) == "Anamea0 Bigholder"


@pytest.mark.parametrize("apostrophe", ["ʼ", "’", "'"])
@pytest.mark.parametrize("name", ["Лук{}яненко", "Мар{}янчук"])
def test_lowercase_continuation_that_is_not_a_suffix_is_part_of_the_name(
    name: str, apostrophe: str
) -> None:
    written = name.format(apostrophe)
    anon_map = _map("uk")
    placeholder = anon_map.add_entity(f"Олена {written}", "PERSON_NAME")

    masked = anon_map.apply_blocklist(f"Пані {written} прийшла.")

    assert masked == f"Пані {placeholder} прийшла."
    assert _restore(placeholder, anon_map) == f"Олена {written}"
    tail = name.split("{}")[1].lower()
    assert tail not in anon_map.token_to_placeholder


@pytest.mark.parametrize(
    ("language", "name", "text", "suffix"),
    [
        ("tr", "Ali Kaya’nın", "Kaya’nın evi", "’nın"),
        ("en", "O'Brien's", "O'Brien's car", "'s"),
    ],
)
def test_known_suffix_still_stays_outside_the_placeholder(
    language: str, name: str, text: str, suffix: str
) -> None:
    anon_map = _map(language)
    placeholder = anon_map.add_entity(name, "PERSON_NAME")

    masked = anon_map.apply_blocklist(text)

    assert masked.startswith(f"{placeholder}{suffix}")


# --------------------------------------------------------------------------
# Case suffixes written after an apostrophe
# --------------------------------------------------------------------------

_CASE_SUFFIXES = (
    "da", "de", "ta", "te", "dan", "den", "tan", "ten", "a", "e", "ya", "ye",
    "ı", "i", "u", "ü", "yı", "yi", "yu", "yü", "la", "le", "yla", "yle",
    "ın", "in", "un", "ün", "nın", "nin", "nun", "nün",
)


@pytest.mark.parametrize("suffix", _CASE_SUFFIXES)
def test_case_suffix_is_stripped_and_is_a_known_tail(suffix: str) -> None:
    from septum_core.text_utils import APOSTROPHE_SUFFIX_TAILS, strip_possessive_suffix

    assert suffix in APOSTROPHE_SUFFIX_TAILS
    assert strip_possessive_suffix(f"Kaya’{suffix}", "tr") == "Kaya"
    assert strip_possessive_suffix(f"Ali Kaya'{suffix}", "tr") == "Ali Kaya"


@pytest.mark.parametrize(
    ("suffixed", "bare"), [("Ali Kaya’dan", "Ali Kaya"), ("Ayşe’ye", "Ayşe"), ("Mehmet'te", "Mehmet")]
)
@pytest.mark.parametrize("suffixed_first", [True, False])
def test_case_suffixed_name_corefers_with_the_bare_name(
    suffixed: str, bare: str, suffixed_first: bool
) -> None:
    anon_map = _map("tr")
    order = (suffixed, bare) if suffixed_first else (bare, suffixed)

    first = anon_map.add_entity(order[0], "PERSON_NAME")
    second = anon_map.add_entity(order[1], "PERSON_NAME")

    assert first == second
    assert _restore(first, anon_map) == bare
    assert anon_map.apply_blocklist(f"{bare.split()[-1]}’dan geldi") == f"{first}’dan geldi"


def test_name_continuations_after_an_apostrophe_are_not_suffix_tails() -> None:
    from septum_core.text_utils import APOSTROPHE_SUFFIX_TAILS

    for continuation in ("яненко", "янчук", "brien", "angelo", "marie"):
        assert continuation not in APOSTROPHE_SUFFIX_TAILS


# --------------------------------------------------------------------------
# A suffix runs to the end of its token
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["Bahá'u'lláh", "Nasr'u'llah", "Habib'u'llah Khan", "Sayf'u'd-Din", "Nasr’u’llah"]
)
@pytest.mark.parametrize("language", ["en", "tr"])
def test_inner_apostrophe_tail_is_part_of_the_name(name: str, language: str) -> None:
    anon_map = _map(language)
    placeholder = anon_map.add_entity(name, "PERSON_NAME")
    text = f"Then {name} spoke."

    masked = anon_map.apply_blocklist(text)

    assert masked == f"Then {' '.join([placeholder] * len(name.split()))} spoke."
    assert _restore(placeholder, anon_map) == name
    assert _restore(text.replace(name, placeholder), anon_map) == text


@pytest.mark.parametrize(
    ("language", "name", "text", "suffix"),
    [
        ("tr", "Ali Kaya", "Kaya’dan geldi", "’dan"),
        ("tr", "Ali Kaya", "Kaya’nın evi", "’nın"),
        ("tr", "Ali Kaya", "Kaya’nın’a", "’nın’a"),
        ("en", "O'Brien", "O'Brien's car", "'s"),
    ],
)
def test_end_of_token_suffixes_are_unchanged(language: str, name: str, text: str, suffix: str) -> None:
    from septum_core.anonymization_map import _apostrophe_suffix_at

    anon_map = _map(language)
    placeholder = anon_map.add_entity(name, "PERSON_NAME")

    assert anon_map.apply_blocklist(text).startswith(f"{placeholder}{suffix}")
    word = text.split()[0]
    assert _apostrophe_suffix_at(word) == len(word) - len(suffix)
