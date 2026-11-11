"""Section titles are masked like chunk text and never leave the server raw.

A title is the opening of a heading line cut to a fixed length, so it can end
inside a value; the heading line is read from the raw text when it is there,
and a cut-off value is found on the title itself otherwise. A raw heading
("T.C. 47382910456") once reached the approval modal as a chunk's title.
"""

from __future__ import annotations

import time
from typing import Optional

import pytest
from septum_api.services import pii_gate
from septum_api.services.anonymization_map import AnonymizationMap
from sanitizer_doubles import PatternDetector, literal
from septum_api.request_limits import MAX_SECTION_TITLE_CHARS
from septum_api.services.chunking import ChunkRecord
from septum_api.services.document_pipeline import _masked_titles


def _mask_title(
    title: Optional[str], source_text: str, amap: Optional[AnonymizationMap]
) -> Optional[str]:
    return pii_gate.section_title_masker(amap)(title, source_text)


def _contract_map() -> AnonymizationMap:
    amap = AnonymizationMap(document_id=1, language="tr")
    amap.add_entity("T", "EMPLOYEE_ID")
    amap.add_entity("47382910456", "NATIONAL_ID")
    amap.add_entity("TR94 0006 2000 1010 0007 1234 56", "IBAN")
    amap.add_entity("Ahmet Çelik", "PERSON_NAME")
    return amap


FIRST_IBAN = "TR94 0006 2000 1010 0007 1234 56"
SECOND_IBAN = "TR94 0006 9999 1010 0007 1234 56"


SECOND_LINE = f"IBAN: {SECOND_IBAN}"


def _two_iban_map() -> AnonymizationMap:
    amap = AnonymizationMap(document_id=1, language="tr")
    amap.add_entity(FIRST_IBAN, "IBAN")
    amap.add_entity(SECOND_IBAN, "IBAN")
    return amap


def _map_of(*pairs: tuple[str, str]) -> AnonymizationMap:
    amap = AnonymizationMap(document_id=1, language="tr")
    for original, entity in pairs:
        amap.add_entity(original, entity)
    return amap


def test_section_title_is_masked_with_the_map() -> None:
    title = _mask_title("T.C. 47382910456", "", _contract_map())

    assert title == "T.C. [NATIONAL_ID_1]"


@pytest.mark.parametrize("cut", [12, 16])
def test_title_cut_mid_value_masks_the_whole_value(cut: int) -> None:
    line = "IBAN: : TR94 0006 2000 1010 0007 1234 56 (maaş hesabı)"

    masked = _mask_title(line[:cut], f"intro\n{line}\nnext", _contract_map())

    assert masked == "IBAN: : [IBAN_1]"


def test_title_without_its_heading_line_masks_a_cut_off_tail() -> None:
    masked = _mask_title("IBAN: : TR94 0006", "", _contract_map())

    assert masked == "IBAN: : [IBAN_1]"


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("KİRACIAhmet Çelik", "KİRACI[PERSON_NAME_1]"),
        ("Kiracı Ahmet Çel", "Kiracı [PERSON_NAME_1]"),
    ],
)
def test_title_glued_or_cut_inside_a_name_masks_the_name(title: str, expected: str) -> None:
    amap = _contract_map()
    amap.token_to_placeholder["ahmet"] = "[PERSON_NAME_1]"

    assert _mask_title(title, "", amap) == expected


def test_long_title_masks_in_linear_time() -> None:
    amap = AnonymizationMap(document_id=1, language="en")
    for i in range(200):
        amap.add_entity(f"Name{i:03d} Surname", "PERSON_NAME")
    mask = pii_gate.section_title_masker(amap)

    started = time.perf_counter()
    mask("ab " * 6_700 + "Name001 Sur", "")
    elapsed = time.perf_counter() - started

    assert elapsed < 0.5


def test_title_without_a_map_is_dropped() -> None:
    assert _mask_title("T.C. 47382910456", "T.C. 47382910456", None) is None
    assert _mask_title(None, "", None) is None


def test_heading_line_decides_which_value_a_cut_title_opens() -> None:
    title = SECOND_LINE[:15]

    assert _mask_title(title, f"x\n{SECOND_LINE}\n", _two_iban_map()) == (
        "IBAN: [IBAN_2]"
    )


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Cinsiyet: K", "Cinsiyet: [SEX_1]"),
        ("Kan Grubu: A+", "Kan Grubu: [BLOOD_GROUP_1]"),
        ("KAYIT T.C. K.", "KAYIT T.C. [SEX_1]."),
        ("KAN GRUBU VE KAYIT", "KAN GRUBU VE KAYIT"),
    ],
)
def test_one_letter_value_standing_alone_in_a_title_is_masked(title: str, expected: str) -> None:
    amap = AnonymizationMap(document_id=1, language="tr")
    amap.add_entity("K", "SEX")
    amap.add_entity("A+", "BLOOD_GROUP")
    amap.add_entity("T", "EMPLOYEE_ID")

    assert _mask_title(title, "", amap) == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("KİRACI AHMET ÇEL", "KİRACI [PERSON_NAME_1]"),
        ("KİRACIAhmet Çel", "KİRACI[PERSON_NAME_1]"),
        ("ÜNVANIACME LOJ", "ÜNVANI[ORGANIZATION_NAME_1]"),
    ],
)
def test_cut_off_value_is_found_whatever_its_case_or_glue(title: str, expected: str) -> None:
    amap = AnonymizationMap(document_id=1, language="tr")
    amap.add_entity("Ahmet Çelik", "PERSON_NAME")
    amap.add_entity("ACME LOJİSTİK", "ORGANIZATION_NAME")

    assert _mask_title(title, "", amap) == expected


def test_glued_tail_skips_a_short_candidate_for_an_open_one() -> None:
    amap = _map_of(("AHMA", "ORGANIZATION_NAME"), ("Ahmet Çelik", "PERSON_NAME"))

    assert _mask_title("KİRACIAHM", "", amap) == "KİRACI[PERSON_NAME_1]"


def test_tail_equal_to_a_short_original_opens_the_longer_one() -> None:
    amap = _map_of(
        ("TR94 0006", "ACCOUNT_NUMBER"), ("TR94 0006 2000 1010 0007 1234 56", "IBAN")
    )

    assert _mask_title("Hesap TR94 0006", "", amap) == "Hesap [IBAN_1]"


def test_glued_tail_never_opens_a_short_single_token() -> None:
    amap = _map_of(("KVKK", "ORGANIZATION_NAME"))

    assert _mask_title("BAŞLIK XKV", "", amap) == "BAŞLIK XKV"


def test_one_letter_tail_opens_nothing() -> None:
    amap = _map_of(("Ahmet Çelik", "PERSON_NAME"))

    assert _mask_title("Ek A", "", amap) == "Ek A"


def test_heading_line_must_start_with_the_title() -> None:
    source = f"Ödeme IBAN: {SECOND_IBAN}\n"

    assert _mask_title("IBAN: TR94 0006 9", source, _two_iban_map()) == (
        "IBAN: [IBAN_2]"
    )


def test_title_that_is_the_whole_heading_line_is_not_cut() -> None:
    amap = _map_of(("Ali Can", "PERSON_NAME"))
    title = "Ad: Ali Can - Ankara Merkez"

    assert _mask_title(title, f"x\n{title}\ny", amap) == (
        "Ad: [PERSON_NAME_1] - Ankara Merkez"
    )


def test_letter_after_a_dot_in_a_title_is_not_a_value() -> None:
    amap = _map_of(("C", "SEX"))

    assert _mask_title("T.C. KAYIT", "", amap) == "T.C. KAYIT"


def test_cut_value_matches_an_original_written_with_other_spacing() -> None:
    amap = _map_of(("TR940006100519786457841326", "IBAN"))

    assert _mask_title("IBAN: TR94 0006", "", amap) == "IBAN: [IBAN_1]"
    assert _mask_title("IBAN: TR940006", "", _map_of(
        ("TR94 0006 1005 1978 6457 8413 26", "IBAN")
    )) == "IBAN: [IBAN_1]"


def test_longer_dotted_fragment_wins_in_a_title() -> None:
    amap = _map_of(("K", "SEX"), ("K.", "EMPLOYEE_ID"))

    assert _mask_title("Kod: K.", "", amap) == "Kod: [EMPLOYEE_ID_1]"


def test_fragment_title_still_reads_its_heading_line() -> None:
    amap = _two_iban_map()
    amap.add_entity("K", "SEX")
    line = f"K {SECOND_LINE}"

    assert _mask_title(line[:17], f"x\n{line}\n", amap) == (
        "[SEX_1] IBAN: [IBAN_2]"
    )


@pytest.mark.parametrize(
    "title",
    ["IBAN: TR94 0006 1005 1978 6457 84", "IBAN: TR94  0006  1005  1978  6457  84"],
)
def test_cut_value_longer_in_characters_than_the_original_masks(title: str) -> None:
    amap = _map_of(("TR940006100519786457841326", "IBAN"))

    assert _mask_title(title, "", amap) == "IBAN: [IBAN_1]"


def test_cut_off_name_with_a_space_in_its_first_letters_masks() -> None:
    amap = _map_of(("Ali Kaya", "PERSON_NAME"))

    assert _mask_title("Kiracı Ali Ka", "", amap) == "Kiracı [PERSON_NAME_1]"


def test_title_cut_right_after_a_label_masks_a_short_value() -> None:
    amap = _map_of(("KAYA", "PERSON_NAME"))

    assert _mask_title("SOYADIKAY", "", amap) == "SOYADI[PERSON_NAME_1]"
    assert _mask_title("XSOYADIKAY", "", amap) == "XSOYADIKAY"


def test_cut_tail_of_a_short_value_at_a_word_start_masks() -> None:
    amap = _map_of(("KAYA", "PERSON_NAME"))

    assert _mask_title("Kiracı KA", "", amap) == "Kiracı [PERSON_NAME_1]"
    assert _mask_title("KAY", "", amap) == "[PERSON_NAME_1]"


def test_label_earlier_in_the_title_does_not_open_a_later_glued_tail() -> None:
    amap = _map_of(("KAYA", "PERSON_NAME"))

    assert _mask_title("SOYADI: x. KİRACIKA", "", amap) == "SOYADI: x. KİRACIKA"


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("No4471KA", "No4471[PERSON_NAME_1]"),
        ("SOYADIKA", "SOYADI[PERSON_NAME_1]"),
        ("XSOYADIKAY", "XSOYADIKAY"),
        ("KİRACIKA", "KİRACIKA"),
    ],
)
def test_cut_tail_of_a_short_value_opens_after_a_digit_or_a_label_only(
    title: str, expected: str
) -> None:
    amap = _map_of(("KAYA", "PERSON_NAME"))

    assert _mask_title(title, "", amap) == expected


def test_cut_tail_in_a_script_without_spaces_masks() -> None:
    amap = AnonymizationMap(document_id=1, language="zh")
    amap.add_entity("张三丰", "PERSON_NAME")

    assert pii_gate.mask_known_entities("联系人张三丰", amap) == "联系人[PERSON_NAME_1]"
    assert _mask_title("联系人张三", "", amap) == "联系人[PERSON_NAME_1]"


def _ingestion_titles(
    text: str, titles: list[tuple[Optional[str], Optional[int], Optional[int]]], *rules
) -> list[Optional[str]]:
    """Titles as the pipeline stores them: projected from the whole document, then cut."""
    detector = PatternDetector(*rules)
    amap = AnonymizationMap(document_id=1, language="tr")
    detection = detector.detect(text, "tr")
    resolved = detector.apply(detection, amap).detected_spans
    records = [
        ChunkRecord(
            index=i, start=0, end=len(text), text=text, strategy="structured",
            section_title=title, section_title_start=start, section_title_end=end,
        )
        for i, (title, start, end) in enumerate(titles)
    ]
    return _masked_titles(detection, resolved, amap, records)


def test_ingestion_title_cut_inside_a_value_is_projected_whole() -> None:
    heading = f"IBAN: {FIRST_IBAN}"
    text = f"{heading}\nAd Soyad: X"

    stored = _ingestion_titles(
        text, [(heading[:15], 0, len(heading))], literal(FIRST_IBAN, "IBAN")
    )

    assert stored == ["IBAN: [IBAN_1]"]
    assert all("TR94" not in title for title in stored)


def test_ingestion_missing_and_empty_titles_are_kept_and_unusable_ones_dropped() -> None:
    text = f"IBAN: {FIRST_IBAN}"

    stored = _ingestion_titles(
        text,
        [(None, None, None), ("", 0, 4), ("IBAN", None, None)],
        literal(FIRST_IBAN, "IBAN"),
    )

    assert stored == [None, "", None]


def test_ingestion_long_title_is_cut_before_a_placeholder_it_would_split() -> None:
    lead = "x" * (MAX_SECTION_TITLE_CHARS - 5)
    heading = f"{lead} {FIRST_IBAN} tail"

    [stored] = _ingestion_titles(
        heading, [(heading[:MAX_SECTION_TITLE_CHARS], 0, len(heading))],
        literal(FIRST_IBAN, "IBAN"),
    )

    assert stored == f"{lead} "
    assert len(stored) <= MAX_SECTION_TITLE_CHARS


def test_ingestion_chunk_under_a_heading_carries_that_headings_masked_title() -> None:
    heading = f"IBAN: {FIRST_IBAN}"
    text = f"{heading}\nAd Soyad: X\ndevam eden metin"
    body_start = len(heading) + 1

    stored = _ingestion_titles(
        text,
        [(heading, 0, len(heading)), (heading, 0, len(heading))],
        literal(FIRST_IBAN, "IBAN"),
    )

    assert body_start < len(text)
    assert stored == ["IBAN: [IBAN_1]", "IBAN: [IBAN_1]"]
    assert all(FIRST_IBAN not in title and "TR94" not in title for title in stored)
