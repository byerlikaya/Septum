"""Person-name masking: shared name parts and joined (hyphen/apostrophe) names.

Two people who share a name part must never collapse into one placeholder,
and every part of a hyphenated or apostrophe name must stay masked when it
recurs on its own. Exercised through the public map API and an unmask
round trip.
"""

from __future__ import annotations

from septum_core.anonymization_map import AnonymizationMap, pick_restoration_original
from septum_core.unmasker import Unmasker


def _map(language: str = "en") -> AnonymizationMap:
    return AnonymizationMap(document_id=1, language=language)


def _restore(text: str, anon_map: AnonymizationMap) -> str:
    return Unmasker().unmask(text, anon_map)


# --------------------------------------------------------------------------
# Shared name parts
# --------------------------------------------------------------------------


def test_second_person_sharing_a_first_name_gets_own_placeholder() -> None:
    anon_map = _map()

    first = anon_map.add_entity("Ali Yılmaz", "PERSON_NAME")
    assert anon_map.add_entity("Ali", "PERSON_NAME") == first
    second = anon_map.add_entity("Ali Kaya", "PERSON_NAME")

    assert second != first
    assert anon_map.apply_blocklist("Kaya and Yılmaz") == f"{second} and {first}"
    assert _restore(f"{first} met {second}", anon_map) == "Ali Yılmaz met Ali Kaya"


def test_shared_token_does_not_claim_either_person() -> None:
    anon_map = _map()

    yilmaz = anon_map.add_entity("Ali Yılmaz", "PERSON_NAME")
    kaya = anon_map.add_entity("Ali Kaya", "PERSON_NAME")
    bare = anon_map.add_entity("Ali", "PERSON_NAME")

    assert kaya != yilmaz
    assert bare not in (yilmaz, kaya)
    assert anon_map.apply_blocklist("ali called") == f"{bare} called"
    assert anon_map.apply_blocklist("Yılmaz, Kaya") == f"{yilmaz}, {kaya}"
    assert _restore(f"{bare}; {yilmaz}; {kaya}", anon_map) == "Ali; Ali Yılmaz; Ali Kaya"


def test_shared_token_in_text_is_masked_before_any_bare_mention() -> None:
    anon_map = _map()

    yilmaz = anon_map.add_entity("Ali Yılmaz", "PERSON_NAME")
    kaya = anon_map.add_entity("Ali Kaya", "PERSON_NAME")
    masked = anon_map.apply_blocklist("Ali was late")

    placeholder = masked.split()[0]
    assert placeholder not in (yilmaz, kaya)
    assert anon_map.add_entity("Ali", "PERSON_NAME") == placeholder
    assert _restore(masked, anon_map) == "Ali was late"


def test_ambiguous_fragment_is_not_extended_to_a_third_person() -> None:
    anon_map = _map()

    anon_map.add_entity("Ali Yılmaz", "PERSON_NAME")
    anon_map.add_entity("Ali Kaya", "PERSON_NAME")
    bare = anon_map.add_entity("Ali", "PERSON_NAME")
    third = anon_map.add_entity("Ali Demir", "PERSON_NAME")

    assert third != bare
    assert _restore(bare, anon_map) == "Ali"
    assert _restore(third, anon_map) == "Ali Demir"


def test_full_name_contradicting_a_known_surname_gets_new_placeholder() -> None:
    anon_map = _map()

    john = anon_map.add_entity("John", "PERSON_NAME")
    assert anon_map.add_entity("John Smith", "PERSON_NAME") == john
    doe = anon_map.add_entity("John Doe", "PERSON_NAME")

    assert doe != john
    assert anon_map.apply_blocklist("Smith and Doe") == f"{john} and {doe}"
    assert _restore(f"{john} / {doe}", anon_map) == "John Smith / John Doe"


def test_full_name_joining_two_known_people_gets_new_placeholder() -> None:
    anon_map = _map()

    ali = anon_map.add_entity("Ali", "PERSON_NAME")
    kaya = anon_map.add_entity("Kaya", "LAST_NAME")
    joined = anon_map.add_entity("Ali Kaya", "PERSON_NAME")

    assert joined not in (ali, kaya)
    assert anon_map.apply_blocklist("Ali and Kaya") == f"{ali} and {kaya}"
    assert _restore(f"{ali}|{kaya}|{joined}", anon_map) == "Ali|Kaya|Ali Kaya"


def test_unambiguous_coreference_still_reuses_one_placeholder() -> None:
    anon_map = _map()

    full = anon_map.add_entity("Ali Yılmaz", "PERSON_NAME")

    assert anon_map.add_entity("Yılmaz", "LAST_NAME") == full
    assert anon_map.add_entity("Ali", "FIRST_NAME") == full
    assert anon_map.add_entity("ali yılmaz", "PERSON_NAME") == full
    assert anon_map.apply_blocklist("ali yılmaz") == f"{full} {full}"
    assert _restore(full, anon_map) == "Ali Yılmaz"


def test_rebuilt_map_applies_the_same_rule() -> None:
    live = _map()
    live.add_entity("Ali Yılmaz", "PERSON_NAME")
    live.add_entity("Ali", "PERSON_NAME")

    rebuilt = AnonymizationMap(
        document_id=1,
        language="en",
        entity_map=dict(live.entity_map),
        token_to_placeholder=dict(live.token_to_placeholder),
        token_counter=dict(live.token_counter),
    )

    assert rebuilt.add_entity("Ali Kaya", "PERSON_NAME") == live.add_entity("Ali Kaya", "PERSON_NAME")
    assert rebuilt.add_entity("Ali Kaya", "PERSON_NAME") != rebuilt.entity_map["Ali Yılmaz"]


# --------------------------------------------------------------------------
# Joined names
# --------------------------------------------------------------------------


def test_hyphenated_name_and_its_parts_are_masked() -> None:
    anon_map = _map()

    placeholder = anon_map.add_entity("Jean-Pierre Dupont", "PERSON_NAME")
    text = "Jean-Pierre wrote. Later Jean, then Pierre, then Dupont."
    masked = anon_map.apply_blocklist(text)

    p = placeholder
    assert masked == f"{p} wrote. Later {p}, then {p}, then {p}."
    assert _restore(p, anon_map) == "Jean-Pierre Dupont"


def test_hyphen_variants_mask_as_one_name() -> None:
    anon_map = _map()

    placeholder = anon_map.add_entity("Jean-Pierre Dupont", "PERSON_NAME")

    assert anon_map.apply_blocklist("Jean‑Pierre left") == f"{placeholder} left"
    assert anon_map.apply_blocklist("jean‐pierre") == placeholder


def test_apostrophe_name_and_its_tail_are_masked_with_any_apostrophe() -> None:
    anon_map = _map()

    placeholder = anon_map.add_entity("O'Brien", "PERSON_NAME")
    masked = anon_map.apply_blocklist("O'Brien, O’Brien and Brien")

    assert masked == f"{placeholder}, {placeholder} and {placeholder}"


def test_typographic_apostrophe_name_matches_ascii_text() -> None:
    anon_map = _map()

    placeholder = anon_map.add_entity("D’Angelo Rossi", "PERSON_NAME")

    assert anon_map.apply_blocklist("D'Angelo met Angelo") == f"{placeholder} met {placeholder}"
    assert _restore(placeholder, anon_map) == "D’Angelo Rossi"


def test_joined_name_keeps_possessive_suffix_outside_placeholder() -> None:
    anon_map = _map()

    placeholder = anon_map.add_entity("O'Brien", "PERSON_NAME")

    assert anon_map.apply_blocklist("O'Brien's car") == f"{placeholder}'s car"


def test_suffixed_name_still_masks_its_stem() -> None:
    anon_map = _map("tr")

    placeholder = anon_map.add_entity("Ahmet Kaya", "PERSON_NAME")

    assert anon_map.apply_blocklist("Kaya’nın evi") == f"{placeholder}’nın evi"


def test_unknown_joined_word_masks_only_its_known_part() -> None:
    anon_map = _map()

    placeholder = anon_map.add_entity("John Smith", "PERSON_NAME")

    assert anon_map.apply_blocklist("Smith-Jones arrived") == f"{placeholder}-Jones arrived"


def test_part_of_a_joined_name_corefers_with_it() -> None:
    anon_map = _map()

    placeholder = anon_map.add_entity("Jean-Pierre Dupont", "PERSON_NAME")

    assert anon_map.add_entity("Jean", "FIRST_NAME") == placeholder
    assert anon_map.add_entity("Jean Pierre Dupont", "PERSON_NAME") == placeholder
    assert _restore(placeholder, anon_map) == "Jean-Pierre Dupont"


def test_joined_name_round_trip() -> None:
    anon_map = _map()
    anon_map.add_entity("Mary-Jane O'Neil", "PERSON_NAME")

    text = "Mary said O’Neil and Jane agree; Mary-Jane too, and Neil."
    masked = anon_map.apply_blocklist(text)

    for part in ("Mary", "Jane", "Neil"):
        assert part not in masked
    assert _restore(masked, anon_map).count("Mary-Jane O'Neil") == 5


def test_restoration_prefers_the_full_joined_name_over_its_part() -> None:
    anon_map = _map()

    placeholder = anon_map.add_entity("Jean-Pierre Dupont", "PERSON_NAME")
    assert anon_map.add_entity("Jean", "FIRST_NAME") == placeholder

    assert pick_restoration_original(["Jean", "Jean-Pierre Dupont"]) == "Jean-Pierre Dupont"
    assert _restore(placeholder, anon_map) == "Jean-Pierre Dupont"


# --------------------------------------------------------------------------
# Apostrophe joins vs apostrophe suffixes
# --------------------------------------------------------------------------


def test_apostrophe_before_a_capital_joins_two_given_names() -> None:
    for name in ("Anne’Marie Dubois", "Anne'Marie Dubois"):
        anon_map = _map()
        placeholder = anon_map.add_entity(name, "PERSON_NAME")

        masked = anon_map.apply_blocklist("Marie, Anne and Anne’Marie")

        assert masked == f"{placeholder}, {placeholder} and {placeholder}"
        assert _restore(placeholder, anon_map) == name


def test_apostrophe_before_lowercase_stays_a_suffix() -> None:
    anon_map = _map("tr")

    placeholder = anon_map.add_entity("Ali Kaya’nın", "PERSON_NAME")

    assert "nın" not in anon_map.token_to_placeholder
    assert "kaya’nın" not in anon_map.token_to_placeholder
    assert anon_map.apply_blocklist("Kaya geldi") == f"{placeholder} geldi"


def test_all_caps_apostrophe_continuation_stays_a_suffix() -> None:
    anon_map = _map("tr")

    placeholder = anon_map.add_entity("ALİ KAYA’NIN", "PERSON_NAME")

    assert anon_map.apply_blocklist("Kaya geldi") == f"{placeholder} geldi"
    assert "nin" not in anon_map.token_to_placeholder
    assert "nın" not in anon_map.token_to_placeholder


# --------------------------------------------------------------------------
# Maps persisted before key folding
# --------------------------------------------------------------------------


def _old_format_payload() -> dict:
    """A map as an older version serialized it: typographic, unfolded keys."""
    return {
        "document_id": 7,
        "language": "en",
        "entity_map": {"Sean O’Brien": "[PERSON_NAME_1]", "Jean‑Luc Picard": "[PERSON_NAME_2]"},
        "token_to_placeholder": {
            "sean": "[PERSON_NAME_1]",
            "o’brien": "[PERSON_NAME_1]",
            "jean‑luc": "[PERSON_NAME_2]",
            "picard": "[PERSON_NAME_2]",
        },
        "token_counter": {"PERSON_NAME": 2},
    }


def test_old_format_map_keys_are_folded_on_load() -> None:
    payload = _old_format_payload()
    anon_map = AnonymizationMap(**payload)

    assert "o'brien" in anon_map.token_to_placeholder
    assert "jean-luc" in anon_map.token_to_placeholder
    assert "o’brien" not in anon_map.token_to_placeholder
    assert anon_map.blocklist == set(anon_map.token_to_placeholder)
    assert payload["token_to_placeholder"] == _old_format_payload()["token_to_placeholder"]


def test_old_format_map_masks_every_apostrophe_and_hyphen_variant() -> None:
    anon_map = AnonymizationMap(**_old_format_payload())

    masked = anon_map.apply_blocklist("O'Brien, O’Brien, Jean-Luc and Jean‐Luc")

    assert masked == "[PERSON_NAME_1], [PERSON_NAME_1], [PERSON_NAME_2] and [PERSON_NAME_2]"
    assert _restore(masked, anon_map) == (
        "Sean O’Brien, Sean O’Brien, Jean‑Luc Picard and Jean‑Luc Picard"
    )


def test_old_format_blocklist_is_folded_too() -> None:
    payload = _old_format_payload()
    anon_map = AnonymizationMap(**payload, blocklist=set(payload["token_to_placeholder"]))

    assert anon_map.blocklist == {"sean", "o'brien", "jean-luc", "picard", "brien", "jean", "luc"}


def test_old_format_map_derives_joined_part_keys() -> None:
    anon_map = AnonymizationMap(**_old_format_payload())

    masked = anon_map.apply_blocklist("Brien, Jean and Luc")

    assert masked == "[PERSON_NAME_1], [PERSON_NAME_2] and [PERSON_NAME_2]"
    assert {"brien", "jean", "luc"} <= anon_map.blocklist
    assert _restore("[PERSON_NAME_1]", anon_map) == "Sean O’Brien"


def test_old_format_part_key_never_steals_a_shared_token() -> None:
    payload = _old_format_payload()
    payload["entity_map"]["Jean"] = "[PERSON_NAME_3]"
    payload["token_to_placeholder"]["jean"] = "[PERSON_NAME_3]"
    payload["token_counter"] = {"PERSON_NAME": 3}

    anon_map = AnonymizationMap(**payload)

    assert anon_map.token_to_placeholder["jean"] == "[PERSON_NAME_3]"
    assert anon_map.token_to_placeholder["luc"] == "[PERSON_NAME_2]"


def test_part_keys_are_not_derived_from_unjoined_keys() -> None:
    anon_map = AnonymizationMap(
        document_id=1, token_to_placeholder={"o kisi": "[PERSON_NAME_1]"}
    )

    assert anon_map.token_to_placeholder == {"o kisi": "[PERSON_NAME_1]"}


# --------------------------------------------------------------------------
# All-caps apostrophe joins
# --------------------------------------------------------------------------


def test_all_caps_apostrophe_join_is_masked_whole() -> None:
    anon_map = _map()

    placeholder = anon_map.add_entity("ANNE’MARIE DUBOIS", "PERSON_NAME")

    assert anon_map.apply_blocklist("ANNE’MARIE and ANNE'MARIE") == f"{placeholder} and {placeholder}"
    assert "marie" not in anon_map.token_to_placeholder
    assert _restore(placeholder, anon_map) == "ANNE’MARIE DUBOIS"


def test_all_caps_suffix_is_masked_with_the_name_and_round_trips() -> None:
    anon_map = _map("tr")
    original = "ALİ KAYA’NIN"
    text = f"{original} evi. KAYA’NIN arabası."

    placeholder = anon_map.add_entity(original, "PERSON_NAME")
    masked = anon_map.apply_blocklist(text.replace(original, placeholder, 1))

    assert masked == f"{placeholder} evi. {placeholder} arabası."
    assert "nın" not in anon_map.token_to_placeholder
    assert "nin" not in anon_map.token_to_placeholder
    assert _restore(f"{placeholder} evi.", anon_map) == f"{original} evi."


def test_mixed_case_suffix_still_keeps_suffix_outside_placeholder() -> None:
    anon_map = _map("tr")

    placeholder = anon_map.add_entity("Ali Kaya’nın", "PERSON_NAME")

    assert anon_map.apply_blocklist("Kaya’nın evi") == f"{placeholder}’nın evi"
    assert "kaya'nın" not in anon_map.token_to_placeholder
