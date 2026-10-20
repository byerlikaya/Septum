"""Placeholder reuse rules of :class:`AnonymizationMap`."""

from __future__ import annotations

from septum_core.anonymization_map import AnonymizationMap
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
