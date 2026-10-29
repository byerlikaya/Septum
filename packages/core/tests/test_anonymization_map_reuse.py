"""Placeholder reuse rules of :class:`AnonymizationMap`."""

from __future__ import annotations

import pytest
from septum_core.anonymization_map import (
    AnonymizationMap,
    _apostrophe_suffix_at,
    _has_apostrophe_suffix as has_apostrophe_suffix,
    _name_tokens,
    pick_restoration_original,
)
from septum_core.span_processing import _tail_starts_with_letter
from septum_core.unmasker import Unmasker


def _map() -> AnonymizationMap:
    return AnonymizationMap(document_id=1, language="en")


def test_two_different_dates_get_two_placeholders() -> None:
    anon_map = _map()

    first = anon_map.add_entity("15 March 2024", "DATE")
    second = anon_map.add_entity("20 March 2024", "DATE")

    assert first == "[DATE_1]"
    assert second == "[DATE_2]"


def test_same_date_twice_reuses_one_placeholder() -> None:
    anon_map = _map()

    assert anon_map.add_entity("15 March 2024", "DATE") == "[DATE_1]"
    assert anon_map.add_entity("15 March 2024", "DATE") == "[DATE_1]"


def test_month_year_and_full_date_get_separate_placeholders() -> None:
    anon_map = _map()

    full = anon_map.add_entity("15 March 2024", "DATE")
    month_year = anon_map.add_entity("March 2024", "DATE")

    assert full != month_year
    restored = Unmasker().unmask(f"{full} then {month_year}", anon_map)
    assert restored == "15 March 2024 then March 2024"


def test_token_subset_across_types_gets_new_placeholder() -> None:
    anon_map = _map()

    address = anon_map.add_entity("Atatürk Caddesi Antalya", "POSTAL_ADDRESS")
    city = anon_map.add_entity("Antalya", "LOCATION")

    assert address == "[POSTAL_ADDRESS_1]"
    assert city == "[LOCATION_1]"


def test_same_normalized_value_of_another_type_gets_new_placeholder() -> None:
    anon_map = _map()

    location = anon_map.add_entity("Antalya", "LOCATION")
    organization = anon_map.add_entity("ANTALYA", "ORGANIZATION_NAME")

    assert location == "[LOCATION_1]"
    assert organization == "[ORGANIZATION_NAME_1]"


def test_identical_string_keeps_its_placeholder_across_types() -> None:
    anon_map = _map()

    location = anon_map.add_entity("Antalya", "LOCATION")

    assert anon_map.add_entity("Antalya", "ORGANIZATION_NAME") == location
    assert anon_map.entity_map == {"Antalya": location}


def test_person_name_coreference_still_reuses() -> None:
    anon_map = _map()

    full = anon_map.add_entity("John Smith", "PERSON_NAME")

    assert anon_map.add_entity("John", "FIRST_NAME") == full
    assert anon_map.add_entity("Smith", "PERSON_NAME") == full


def test_possessive_still_matches_the_name() -> None:
    anon_map = _map()

    name = anon_map.add_entity("Ahmet", "PERSON_NAME")

    assert anon_map.add_entity("Ahmet's", "PERSON_NAME") == name


def test_person_tokens_do_not_merge_into_other_types() -> None:
    anon_map = _map()

    org = anon_map.add_entity("Smith Holdings", "ORGANIZATION_NAME")

    assert anon_map.add_entity("Smith", "PERSON_NAME") == "[PERSON_NAME_1]"
    assert org == "[ORGANIZATION_NAME_1]"


def test_map_restored_from_persisted_fields_applies_type_rule() -> None:
    anon_map = AnonymizationMap(
        document_id=2,
        language="en",
        entity_map={"15 March 2024": "[DATE_1]", "John Smith": "[PERSON_NAME_1]"},
        token_to_placeholder={"john": "[PERSON_NAME_1]", "smith": "[PERSON_NAME_1]"},
        token_counter={"DATE": 1, "PERSON_NAME": 1},
    )

    assert anon_map.blocklist == {"john", "smith"}
    assert anon_map.add_entity("20 March 2024", "DATE") == "[DATE_2]"
    assert anon_map.add_entity("John", "PERSON_NAME") == "[PERSON_NAME_1]"


def test_entries_written_directly_are_found_after_index_sync() -> None:
    anon_map = _map()
    anon_map.entity_map["Antalya"] = "[LOCATION_4]"

    assert anon_map.add_entity("ANTALYA", "LOCATION") == "[LOCATION_4]"


def test_many_non_person_entities_stay_fast() -> None:
    import datetime
    import time

    anon_map = _map()
    start_day = datetime.date(2000, 1, 1)
    values = [
        (start_day + datetime.timedelta(days=i)).strftime("%d %B %Y")
        for i in range(5000)
    ]

    started = time.perf_counter()
    placeholders = {anon_map.add_entity(v, "DATE") for v in values}
    elapsed = time.perf_counter() - started

    assert len(placeholders) == 5000
    assert elapsed < 1.0


def test_direct_write_after_add_entity_is_indexed_for_variants() -> None:
    anon_map = _map()
    anon_map.add_entity("1 March 2024", "DATE")
    anon_map.entity_map["Antalya"] = "[LOCATION_4]"
    anon_map.entity_map["Jane Roe"] = "[PERSON_NAME_3]"

    assert anon_map.add_entity("ANTALYA", "LOCATION") == "[LOCATION_4]"
    assert anon_map.add_entity("Roe", "PERSON_NAME") == "[PERSON_NAME_3]"
    assert anon_map.add_entity("1 March 2024", "DATE") == "[DATE_1]"


def test_directly_written_date_is_not_merged_into_another_date() -> None:
    anon_map = _map()
    anon_map.add_entity("1 March 2024", "DATE")
    anon_map.entity_map["2 March 2024"] = "[DATE_2]"

    assert anon_map.add_entity("2 March 2024", "DATE") == "[DATE_2]"
    assert anon_map.add_entity("1 March 2024", "DATE") == "[DATE_1]"


def test_surface_form_with_possessive_keeps_its_key_and_shares_the_placeholder() -> None:
    anon_map = AnonymizationMap(document_id=1, language="tr")

    placeholder = anon_map.add_entity("Ayşe Kaya", "PERSON_NAME")

    assert anon_map.add_entity("Ayşe Kaya’nın", "PERSON_NAME") == placeholder
    assert anon_map.entity_map == {
        "Ayşe Kaya": placeholder,
        "Ayşe Kaya’nın": placeholder,
    }
    assert Unmasker().unmask(f"{placeholder}’nın", anon_map) == "Ayşe Kaya’nın"


def test_reverse_lookup_restores_the_first_registered_original() -> None:
    anon_map = AnonymizationMap(document_id=1, language="tr")

    placeholder = anon_map.add_entity("Ayşe Kaya", "PERSON_NAME")
    anon_map.add_entity("AYŞE KAYA", "PERSON_NAME")

    assert Unmasker().unmask(placeholder, anon_map) == "Ayşe Kaya"


def test_variant_surface_keys_are_all_registered_for_masking() -> None:
    anon_map = AnonymizationMap(document_id=1, language="tr")

    acme = anon_map.add_entity("Acme Lojistik", "ORGANIZATION_NAME")
    assert anon_map.add_entity("ACME LOJİSTİK", "ORGANIZATION_NAME") == acme
    assert anon_map.add_entity("Acme  Lojistik", "ORGANIZATION_NAME") == acme

    assert anon_map.entity_map == {
        "Acme Lojistik": acme,
        "ACME LOJİSTİK": acme,
        "Acme  Lojistik": acme,
    }


def test_coreference_tokens_drop_a_folded_suffix_glued_to_a_name() -> None:
    anon_map = AnonymizationMap(document_id=1, language="tr")

    placeholder = anon_map.add_entity("Ali Kaya’nın Ahmet", "PERSON_NAME")

    assert anon_map.blocklist == {"ali", "kaya", "ahmet"}
    assert anon_map.token_to_placeholder["kaya"] == placeholder
    assert "kaya’nın" not in anon_map.blocklist
    assert anon_map.apply_blocklist("Kaya nerede?") == f"{placeholder} nerede?"


def test_later_bare_surname_reuses_the_name_placeholder_after_a_suffixed_key() -> None:
    anon_map = AnonymizationMap(document_id=1, language="tr")

    placeholder = anon_map.add_entity("Ali Kaya’nın", "PERSON_NAME")

    assert anon_map.add_entity("Kaya", "PERSON_NAME") == placeholder
    assert anon_map.add_entity("Ali Kaya", "PERSON_NAME") == placeholder


def test_name_tokens_cut_at_an_apostrophe_but_keep_a_leading_one() -> None:
    assert _name_tokens("’Kaya Ali’nın", "tr") == ["’kaya", "ali"]
    assert _name_tokens("Kaya'nın", "tr") == ["kaya"]


def test_suffixed_variant_registered_first_restores_the_bare_name() -> None:
    anon_map = AnonymizationMap(document_id=1, language="tr")

    placeholder = anon_map.add_entity("Ali Kaya’nın", "PERSON_NAME")
    anon_map.add_entity("Ali Kaya", "PERSON_NAME")

    assert Unmasker().unmask(f"{placeholder}’nın", anon_map) == "Ali Kaya’nın"
    assert Unmasker().unmask(placeholder, anon_map) == "Ali Kaya"


def test_alias_registered_after_the_full_name_does_not_displace_it() -> None:
    anon_map = AnonymizationMap(document_id=1, language="tr")

    placeholder = anon_map.add_entity("Ali Yılmaz", "PERSON_NAME")
    assert anon_map.add_entity("Ali", "PERSON_NAME") == placeholder

    assert Unmasker().unmask(placeholder, anon_map) == "Ali Yılmaz"


def test_pick_restoration_original_prefers_the_bare_form_then_the_first() -> None:
    assert pick_restoration_original(["Ali Kaya’nın", "Ali Kaya"]) == "Ali Kaya"
    assert pick_restoration_original(["Kaya’nın"]) == "Kaya’nın"
    assert pick_restoration_original(["Ali Yılmaz", "Ali"]) == "Ali Yılmaz"


def test_apostrophe_without_a_following_letter_is_not_a_suffix() -> None:
    assert not has_apostrophe_suffix("Ali Kaya’")
    assert not has_apostrophe_suffix("’Kaya")
    assert has_apostrophe_suffix("Ali Kaya’nın")


def test_apostrophe_in_a_name_is_not_a_suffix_so_the_token_stays_whole() -> None:
    assert _name_tokens("O'Brien", "en") == ["o'brien"]
    assert _name_tokens("D’Angelo", "en") == ["d’angelo"]
    assert _name_tokens("d'Arc", "fr") == ["d'arc"]
    assert _name_tokens("Sa'id", "ar") == ["sa'id"]
    assert _name_tokens("Sa’id", "ar") == ["sa’id"]


def test_suffix_cut_needs_a_lowercase_letter_after_the_apostrophe() -> None:
    assert _name_tokens("Kaya’nın", "tr") == ["kaya"]
    assert _name_tokens("Kaya’1", "tr") == ["kaya’1"]


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("Sean O'Brien", "Sean Murphy"),
        ("Sean O’Brien", "Sean Murphy"),
        ("Marco D'Angelo", "Marco Rossi"),
        ("Marco D’Angelo", "Marco Rossi"),
        ("Jeanne d'Arc", "Jeanne Moreau"),
        ("Sa'id Ahmed", "Ahmed Khan"),
        ("Sa’id Ahmed", "Ahmed Khan"),
    ],
)
def test_apostrophe_names_stay_distinct_from_another_person(first: str, second: str) -> None:
    anon_map = _map()

    first_placeholder = anon_map.add_entity(first, "PERSON_NAME")
    second_placeholder = anon_map.add_entity(second, "PERSON_NAME")

    assert first_placeholder != second_placeholder
    assert anon_map.entity_map == {first: first_placeholder, second: second_placeholder}
    assert Unmasker().unmask(f"{first_placeholder} / {second_placeholder}", anon_map) == f"{first} / {second}"


def test_has_apostrophe_suffix_uses_the_same_rule_as_name_tokens() -> None:
    assert has_apostrophe_suffix("Kaya’nın")
    assert not has_apostrophe_suffix("O’Brien")
    assert not has_apostrophe_suffix("Sa'id")
    assert not has_apostrophe_suffix("Kaya’1")


def test_non_person_suffixed_surface_shares_the_placeholder() -> None:
    anon_map = AnonymizationMap(document_id=1, language="tr")

    location = anon_map.add_entity("Kaya", "LOCATION")

    assert anon_map.add_entity("Kaya’nın", "LOCATION") == location
    assert anon_map.entity_map == {"Kaya": location, "Kaya’nın": location}


def test_blocklist_on_the_reuse_path_redacts_the_new_surname() -> None:
    anon_map = _map()

    placeholder = anon_map.add_entity("John", "PERSON_NAME")

    assert anon_map.add_entity("John Smith", "PERSON_NAME") == placeholder
    assert anon_map.token_to_placeholder["smith"] == placeholder
    assert anon_map.apply_blocklist("Smith said") == f"{placeholder} said"


def test_two_character_name_tokens_are_not_blocklisted() -> None:
    anon_map = _map()

    anon_map.add_entity("Al Smith", "PERSON_NAME")

    assert anon_map.blocklist == {"smith"}
    assert anon_map.token_to_placeholder == {"smith": "[PERSON_NAME_1]"}


def test_bare_name_restores_the_most_complete_form_whatever_the_order() -> None:
    assert pick_restoration_original(["Smith", "John Smith"]) == "John Smith"
    assert pick_restoration_original(["John Smith", "Smith"]) == "John Smith"
    assert pick_restoration_original(["Ali", "Ali Yılmaz"]) == "Ali Yılmaz"


def test_per_document_restore_uses_the_full_name_when_the_alias_comes_first() -> None:
    anon_map = AnonymizationMap(document_id=2, language="en")
    anon_map.entity_map = {"Smith": "[PERSON_NAME_1]", "John Smith": "[PERSON_NAME_1]"}

    assert Unmasker().unmask("[PERSON_NAME_1] paid", anon_map) == "John Smith paid"


def test_bare_forms_outside_one_name_chain_restore_the_first_registered() -> None:
    assert pick_restoration_original(["Smith", "Jane Mary Smith", "John Smith"]) == "Smith"
    assert pick_restoration_original(["John Smith", "Jane Mary Smith"]) == "John Smith"


def test_pick_falls_back_to_the_first_original_when_every_candidate_is_suffixed() -> None:
    assert pick_restoration_original(["Ali Kaya’nın", "Kaya’nın Ali"]) == "Ali Kaya’nın"
    assert pick_restoration_original(["Kaya’nın", "Ali Kaya’nın"]) == "Kaya’nın"


def test_suffix_on_any_whitespace_token_counts() -> None:
    assert has_apostrophe_suffix("Kaya’nın Ali")
    assert has_apostrophe_suffix("ALİ KAYA’NIN")
    assert not has_apostrophe_suffix("ALİ KAYA")


def test_capital_tail_on_a_long_head_stays_whole() -> None:
    assert _apostrophe_suffix_at("Anne’Marie") is None
    assert not has_apostrophe_suffix("Anne’Marie")


def test_first_suffix_apostrophe_is_the_cut_point() -> None:
    assert _apostrophe_suffix_at("kaya’nın’a") == 4


def test_exactly_three_character_head_is_cut() -> None:
    assert _apostrophe_suffix_at("Ali’nın") == 3


def test_one_letter_suffix_is_cut_at_its_apostrophe() -> None:
    assert _apostrophe_suffix_at("Kaya’s") == 4


def test_digit_tail_is_not_folded_on_the_right_side() -> None:
    assert not _tail_starts_with_letter("Kaya’1234", 4)
    assert _tail_starts_with_letter("Kaya’nın", 4)
