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
from septum_api.services.chunking_strategy import Chunk as SemanticChunk
from septum_api.services.document_pipeline import _mask_section_titles, _sanitize_chunks
from septum_core.spans import SanitizeResult


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


class _LabelledIbanSanitizer:
    """Masks only the IBAN value, as the real sanitizer would."""

    def sanitize(self, text: str, language: str, anon_map: AnonymizationMap) -> SanitizeResult:
        value = "TR94 0006 2000 1010 0007 1234 56"
        placeholder = anon_map.add_entity(value, "IBAN")
        return SanitizeResult(sanitized_text=text.replace(value, placeholder), entity_count=1)


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


def test_ingestion_masks_every_title_once_the_map_is_complete() -> None:
    amap = _contract_map()
    raw = ["IBAN: : TR94 0006 2000 1010 0007 1234 56\nAd Soyad: Ahmet Çelik", "devam eden metin"]
    title = "IBAN: : TR94 0006 2000 1010 0007 1234 56"
    chunks = [
        SemanticChunk(text="IBAN: : [IBAN_1]\nAd Soyad: [PERSON_NAME_1]", index=0, section_title=title),
        SemanticChunk(text="devam eden metin", index=1, section_title=title),
    ]

    _mask_section_titles(chunks, raw, amap)

    assert [c.section_title for c in chunks] == ["IBAN: : [IBAN_1]", "IBAN: : [IBAN_1]"]


def test_ingestion_title_in_no_raw_text_and_missing_title() -> None:
    chunks = [
        SemanticChunk(text="a", index=0, section_title="Ad Soyad: Ahmet Çelik, IBAN TR94 0006"),
        SemanticChunk(text="b", index=1, section_title=None),
    ]

    _mask_section_titles(chunks, ["a", "b"], _contract_map())

    assert [c.section_title for c in chunks] == ["Ad Soyad: [PERSON_NAME_1], IBAN [IBAN_1]", None]


async def test_sanitize_chunks_stores_masked_titles() -> None:
    title = "IBAN: TR94 0006 2000 1010 0007 1234 56"
    raw = f"{title}\nAd Soyad: X"
    chunk = SemanticChunk(text=raw, index=0, section_title=title)

    result = await _sanitize_chunks(
        _LabelledIbanSanitizer(), [chunk], [raw], "tr",
        AnonymizationMap(document_id=1, language="tr"), lambda _: None,
    )

    [stored] = result.stored_chunks
    assert stored.section_title == "IBAN: [IBAN_1]"
    assert "TR94" not in stored.section_title


def test_heading_line_decides_which_value_a_cut_title_opens() -> None:
    title = SECOND_LINE[:15]

    assert _mask_title(title, f"x\n{SECOND_LINE}\n", _two_iban_map()) == (
        "IBAN: [IBAN_2]"
    )


def test_ingestion_reads_the_heading_line_from_the_chunk_that_holds_it() -> None:
    title = SECOND_LINE[:15]
    chunks = [
        SemanticChunk(text="a", index=0, section_title=title),
        SemanticChunk(text="b", index=1, section_title=title),
    ]

    _mask_section_titles(chunks, ["no heading here", f"x\n{SECOND_LINE}\n"], _two_iban_map())

    assert [c.section_title for c in chunks] == ["IBAN: [IBAN_2]", "IBAN: [IBAN_2]"]


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
