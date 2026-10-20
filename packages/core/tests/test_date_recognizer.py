"""Tests for the shared date grammar, ``CalendarDateRecognizer`` and DATE wiring."""

from __future__ import annotations

import importlib
import pkgutil

import pytest
import septum_core.recognizers as recognizer_packs
from septum_core import SeptumCoreConfig, SeptumEngine
from septum_core.detector import CalendarDateRecognizer, DateOfBirthRecognizer
from septum_core.ports import NullSemanticDetectionPort
from septum_core.regulations.composer import ComposedPolicy


def _engine(entity_types: list[str]) -> SeptumEngine:
    policy = ComposedPolicy(
        entity_types=entity_types,
        recognizers=[],
        regulation_ids=[],
        non_pii_rules=[],
    )
    return SeptumEngine(
        config=SeptumCoreConfig(use_presidio_layer=True, use_ner_layer=False),
        semantic_port=NullSemanticDetectionPort(),
        policy=policy,
    )


@pytest.fixture(scope="module")
def date_engine() -> SeptumEngine:
    return _engine(["DATE", "DATE_OF_BIRTH", "PHONE_NUMBER"])


def _dates(text: str) -> list[str]:
    results = CalendarDateRecognizer().analyze(text, entities=["DATE"])
    return [text[r.start : r.end] for r in results]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Admitted 2024-03-15 for review.", "2024-03-15"),
        ("Admitted 15.03.2024 for review.", "15.03.2024"),
        ("Admitted 03/15/2024 for review.", "03/15/2024"),
        ("Admitted 15-03-24 for review.", "15-03-24"),
        ("Admitted 15 March 2024 for review.", "15 March 2024"),
        ("Admitted August 15, 1980 for review.", "August 15, 1980"),
        ("Kontrol 15 Mart 2024 tarihinde.", "15 Mart 2024"),
        ("KONTROL 3 MAYIS 2020 TARİHİNDE.", "3 MAYIS 2020"),
        ("Termin am 22. Juli 1991 bestätigt.", "22. Juli 1991"),
        ("Rendez-vous le 09 février 1987 confirmé.", "09 février 1987"),
        ("Consulta el 01 de octubre de 2024 confirmada.", "01 de octubre de 2024"),
        ("Consulta em 12 de março de 1983 marcada.", "12 de março de 1983"),
        ("Visita il 15 maggio 2024 fissata.", "15 maggio 2024"),
        ("Приём 15 марта 2024 назначен.", "15 марта 2024"),
        ("Wizyta 15 stycznia 2024 potwierdzona.", "15 stycznia 2024"),
        ("Ραντεβού 15 Ιανουαρίου 2024 επιβεβαιώθηκε.", "15 Ιανουαρίου 2024"),
    ],
)
def test_full_dates_match_across_formats_and_languages(
    text: str, expected: str
) -> None:
    assert _dates(text) == [expected]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Discharged in March 2024.", "March 2024"),
        ("Taburcu: Mayıs 2020.", "Mayıs 2020"),
        ("Выписан в март 2024.", "март 2024"),
    ],
)
def test_month_name_with_year_and_no_day_matches(text: str, expected: str) -> None:
    assert _dates(text) == [expected]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Seen on 15 March for review.", ["15 March"]),
        ("Seen on March 15 for review.", ["March 15"]),
        ("Kontrol 15 Mart günü yapıldı.", ["15 Mart"]),
        ("Termin am 22. Juli bestätigt.", ["22. Juli"]),
        ("Stayed from 15 March to 24 April 2024.", ["15 March", "24 April 2024"]),
        ("Stayed 15–24 March 2024 on the ward.", ["15–24 March 2024"]),
        ("Stayed 15-24 March on the ward.", ["15-24 March"]),
        ("Stayed March 15–24, 2024 on the ward.", ["March 15–24, 2024"]),
        ("Stayed 15 March 24 April 2024.", ["15 March", "24 April 2024"]),
    ],
)
def test_day_and_month_without_year_and_ranges_match(
    text: str, expected: list[str]
) -> None:
    assert _dates(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Seen 5/6/24 in clinic.", "5/6/24"),
        ("Seen 5-6-24 in clinic.", "5-6-24"),
        ("Seen 12/31/24 in clinic.", "12/31/24"),
        ("Seen 05.06.24 in clinic.", "05.06.24"),
    ],
)
def test_unpadded_short_year_numerics_match_with_slash_or_dash(
    text: str, expected: str
) -> None:
    assert _dates(text) == [expected]


@pytest.mark.parametrize(
    "text",
    [
        "Version 5/6 is out.",
        "Rated 5/6 by reviewers.",
        "Released as 5.6.24 yesterday.",
        "Invalid stamp 13/13/24 found.",
        "Invalid stamp 5/32/24 found.",
        "In March we met again.",
        "March 155 was the code.",
    ],
)
def test_partial_or_invalid_numerics_and_bare_months_are_rejected(text: str) -> None:
    assert _dates(text) == []


def test_lowercase_short_month_name_without_year_or_cue_is_not_a_date() -> None:
    assert _dates("The 5 may be wrong.") == []
    assert _dates("seen on 5 may for review.") == ["5 may"]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Follow-up 15 May confirmed.", ["15 May"]),
        ("Admission: 3 Mai erfolgt.", ["3 Mai"]),
        ("Seen 15 Jan. again.", ["15 Jan"]),
        ("Seen 15 jan. again.", ["15 jan"]),
        ("Follow-up May 15 confirmed.", ["May 15"]),
        ("Follow-up Jan. 15 confirmed.", ["Jan. 15"]),
    ],
)
def test_capitalised_or_dotted_weak_name_with_day_is_a_date(
    text: str, expected: list[str]
) -> None:
    assert _dates(text) == expected


def test_abbreviation_homograph_after_a_count_is_over_masked() -> None:
    assert _dates("Score was 3 out of 5.") == ["3 out"]
    assert _dates("order 15 ogo units.") == ["15 ogo"]


def test_capitalised_month_homograph_with_count_is_over_masked() -> None:
    assert _dates("Bought 3 Mac laptops.") == ["3 Mac"]
    assert _dates("Order 15 Ogo units.") == ["15 Ogo"]
    assert _dates("Need 12 Marc pens.") == ["12 Marc"]


@pytest.mark.parametrize(
    "text",
    [
        "The study ran in 2024.",
        "Released as 1.2.30 yesterday.",
        "Upgrade to version 3.12.10 now.",
        "Build v1.2.30 shipped.",
        "Invalid stamp 13/13/2024 found.",
        "Invalid stamp 2024-13-45 found.",
        "Host 192.168.10.12 replied.",
        "Host 10.10.10.10 replied.",
        "We may proceed with the march on Monday.",
        "MAY and MARCH are just words here.",
    ],
)
def test_non_dates_are_rejected(text: str) -> None:
    assert _dates(text) == []


def test_birth_context_yields_single_date_of_birth_span(
    date_engine: SeptumEngine,
) -> None:
    text = "Patient date of birth: 15/03/1990 registered."
    result = date_engine.mask(text, language="en")

    assert [s.entity_type for s in result.detected_spans] == ["DATE_OF_BIRTH"]
    assert "15/03/1990" not in result.masked_text


def test_date_without_birth_context_is_masked_as_date(
    date_engine: SeptumEngine,
) -> None:
    text = "Follow-up visit on 15 March 2024 confirmed."
    result = date_engine.mask(text, language="en")

    assert [s.entity_type for s in result.detected_spans] == ["DATE"]
    assert "[DATE_1]" in result.masked_text


def test_no_date_spans_when_policy_lacks_date() -> None:
    engine = _engine(["DATE_OF_BIRTH", "EMAIL_ADDRESS"])

    text = "Follow-up visit on 15 March 2024 confirmed."
    result = engine.mask(text, language="en")

    assert result.detected_spans == []
    assert "15 March 2024" in result.masked_text


def test_recognizer_skips_when_date_not_requested() -> None:
    recognizer = CalendarDateRecognizer()
    assert recognizer.analyze("On 2024-03-15.", entities=["EMAIL_ADDRESS"]) == []


def test_phone_number_wins_over_overlapping_date(date_engine: SeptumEngine) -> None:
    text = "Call +49 15.03.2024 today."
    assert _dates(text) == ["15.03.2024"]

    result = date_engine.mask(text, language="en")

    assert [s.entity_type for s in result.detected_spans] == ["PHONE_NUMBER"]
    assert "15.03.2024" not in result.masked_text


def test_date_of_birth_recognizer_shares_the_grammar() -> None:
    text = "DOB: March 1990. The follow-up appointment was set for 15 March 2024"
    results = DateOfBirthRecognizer().analyze(text, entities=["DATE_OF_BIRTH"])

    assert [text[r.start : r.end] for r in results] == ["March 1990"]


def test_only_hipaa_pack_declares_date() -> None:
    packs_with_date = []
    for module in pkgutil.iter_modules(recognizer_packs.__path__):
        if not module.ispkg:
            continue
        pack = importlib.import_module(f"{recognizer_packs.__name__}.{module.name}")
        if "DATE" in getattr(pack, "ENTITY_TYPES", ()):
            packs_with_date.append(module.name)

    assert packs_with_date == ["hipaa"]


def test_range_masks_every_date_element(date_engine: SeptumEngine) -> None:
    result = date_engine.mask("Admitted from 15 March to 24 April 2024.", language="en")

    assert [s.entity_type for s in result.detected_spans] == ["DATE", "DATE"]
    assert "15 March" not in result.masked_text
    assert "24 April" not in result.masked_text


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Seen Mar 15, 2024 in clinic.", ["Mar 15, 2024"]),
        ("Seen 15-Mar-2024 in clinic.", ["15-Mar-2024"]),
        ("Seen 15 Jan. 2024 in clinic.", ["15 Jan. 2024"]),
        ("Seen Jan. 2024 in clinic.", ["Jan. 2024"]),
        ("Seen Mar 2024 in clinic.", ["Mar 2024"]),
        ("Gesehen am 3. Okt. 2024 hier.", ["3. Okt. 2024"]),
        ("Vu le 15 janv. 2024 ici.", ["15 janv. 2024"]),
        ("Visited 15 Ogo for review.", ["15 Ogo"]),
    ],
)
def test_abbreviated_month_names_match_next_to_day_or_year(
    text: str, expected: list[str]
) -> None:
    assert _dates(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Need 12 marc pens.",
        "bought 3 mac laptops.",
        "We set 2024 goals.",
        "Version 5/6 is out. Next",
    ],
)
def test_short_or_folded_names_without_corroboration_are_rejected(text: str) -> None:
    assert _dates(text) == []


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Logged 2024-03-15T10:30:00Z by system.", "2024-03-15T10:30:00Z"),
        ("Logged 2024-03-15T10:30:00+02:00 by system.", "2024-03-15T10:30:00+02:00"),
        ("Logged 2024-03-15 10:30 by system.", "2024-03-15 10:30"),
        ("File scan_2024-03-15_final.pdf saved.", "2024-03-15"),
        ("File scan_20240315.pdf saved.", "20240315"),
        ("Stamp 20240315 recorded.", "20240315"),
    ],
)
def test_iso_datetime_and_compact_dates_match(text: str, expected: str) -> None:
    assert _dates(text) == [expected]


@pytest.mark.parametrize(
    "text",
    [
        "Account 120240315 opened.",
        "Account 202403151 opened.",
        "Code ID20240315 issued.",
        "Stamp 20241301 recorded.",
        "Stamp 20240230 recorded.",
        "Stamp 18990101 recorded.",
    ],
)
def test_compact_digit_runs_that_are_not_dates_are_rejected(text: str) -> None:
    assert _dates(text) == []


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Seen March 15th, 2024 in clinic.", ["March 15th, 2024"]),
        ("Seen on the 15th of March again.", ["15th of March"]),
        ("Seen 1st–3rd May 2024 on ward.", ["1st–3rd May 2024"]),
        ("Vu le 1er mars 2024 ici.", ["1er mars 2024"]),
        ("Gezien op 15e maart.", ["15e maart"]),
        ("Sedd 15:e mars 2024 här.", ["15:e mars 2024"]),
        ("Visto el 15º de marzo de 2024.", ["15º de marzo de 2024"]),
    ],
)
def test_ordinal_days_match(text: str, expected: list[str]) -> None:
    assert _dates(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Patient seen 3/15 in clinic.", "3/15"),
        ("Admitted on 15/03 overnight.", "15/03"),
        ("Termin am 15.03. erledigt.", "15.03."),
        ("Ödeme 15.03 tarihinde yapıldı.", "15.03"),
        ("Tarih: 15.03. onaylandı.", "15.03."),
    ],
)
def test_numeric_day_month_without_year_needs_a_date_cue(
    text: str, expected: str
) -> None:
    assert _dates(text) == [expected]


@pytest.mark.parametrize(
    "text",
    [
        "Add 3/4 cup of flour.",
        "Pain was 5/10 overall.",
        "Rated 5/6 by reviewers.",
        "Dose at 2.5 mg twice.",
        "Treffen bis 10.30 Uhr.",
        "Visit on 13/13 failed.",
        "Visit on 15.03 failed.",
    ],
)
def test_fractions_scores_and_times_stay_unmatched(text: str) -> None:
    assert _dates(text) == []


def test_one_scan_serves_both_types() -> None:
    text = "DOB: 15/03/1990. The follow-up appointment was set for 20 March 2024."
    results = CalendarDateRecognizer().analyze(text, entities=["DATE", "DATE_OF_BIRTH"])

    assert [(r.entity_type, text[r.start : r.end]) for r in results] == [
        ("DATE_OF_BIRTH", "15/03/1990"),
        ("DATE", "20 March 2024"),
    ]


def test_long_whitespace_runs_do_not_join_a_date() -> None:
    assert _dates("15" + " " * 50 + "March 2024") == ["March 2024"]


def test_month_vocabulary_covers_languages_and_keeps_babel_isolated() -> None:
    from babel import Locale
    from septum_core import date_grammar

    def snapshot() -> dict:
        return {
            code: dict(Locale.parse(code).months["stand-alone"]["wide"])
            for code in ("id", "hi", "ru", "pl", "az")
        }

    before = snapshot()
    date_grammar._vocabulary.cache_clear()
    date_grammar.warm_up()

    assert snapshot() == before
    assert before["id"][1] == "Januari"
    assert before["ru"][3] == "март"
    assert date_grammar.month_name_count() >= 900
    strong = date_grammar._month_names().strong
    strong_keys = {date_grammar._name_key(n) for n in strong}
    for name in ("march", "mart", "märz", "marzo", "марта", "marca", "ιανουαρίου"):
        assert date_grammar._name_key(name) in strong_keys


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Admission 12/05 to ward.", ["12/05"]),
        ("Discharged: 14/05 home.", ["14/05"]),
        ("Surgery 3/15 uneventful.", ["3/15"]),
        ("Patient died 3/15 overnight.", ["3/15"]),
        ("Follow-up 4/20 booked.", ["4/20"]),
        ("DOS 4/20 billed.", ["4/20"]),
        ("Admitted 12/05–14/05 on ward.", ["12/05–14/05"]),
        ("Giriş: 12.03. yapıldı.", ["12.03."]),
        ("Aufnahme 12.03. erfolgt.", ["12.03."]),
        ("Opname 12/03 gepland.", ["12/03"]),
        ("Inskriven 12/03 igen.", ["12/03"]),
        ("Indlagt 12/03 igen.", ["12/03"]),
        ("Innlagt 12/03 igen.", ["12/03"]),
        ("Przyjęty 12/03 na oddział.", ["12/03"]),
        ("Поступил 12.03. в отделение.", ["12.03."]),
        ("الدخول 12/03 للمستشفى", ["12/03"]),
        ("患者于3/15入院。", ["3/15"]),
        ("3/15に入院した。", ["3/15"]),
        ("입원 3/15 예정", ["3/15"]),
    ],
)
def test_clinical_and_multilingual_cues_mark_day_month(
    text: str, expected: list[str]
) -> None:
    assert _dates(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "12/05–14/05 split evenly.",
        "Pain at 5/10 today.",
        "Admission 13/13 failed.",
        "Admission 12/05–14/13 failed.",
    ],
)
def test_ranges_and_scores_without_valid_cue_stay_unmatched(text: str) -> None:
    assert _dates(text) == []


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Seen 15 mar for review.", ["15 mar"]),
        ("Visto el 12 ene en consulta.", ["12 ene"]),
        ("Vista il 3 gen in ambulatorio.", ["3 gen"]),
        ("Seen 2 feb again.", ["2 feb"]),
    ],
)
def test_lowercase_abbreviation_after_a_day_is_a_date(
    text: str, expected: list[str]
) -> None:
    assert _dates(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "The 5 may be wrong.",
        "Only 3 mai left.",
        "We had 2 maj here.",
        "Ask 3 mac users.",
    ],
)
def test_lowercase_short_wide_name_homographs_still_need_corroboration(
    text: str,
) -> None:
    assert _dates(text) == []


@pytest.mark.parametrize(
    "text, expected",
    [
        ("入院日期2024年3月15日。", ["2024年3月15日"]),
        ("于3月15日出院。", ["3月15日"]),
        ("2024年3月に入院。", ["2024年3月"]),
        ("2024 年 3 月 15 日", ["2024 年 3 月 15 日"]),
        ("3月15号复诊。", ["3月15号"]),
        ("2024년 3월 15일 입원", ["2024년 3월 15일"]),
        ("日期2024-03-15。", ["2024-03-15"]),
        ("วันที่ 15 มีนาคม 2567", ["15 มีนาคม 2567"]),
    ],
)
def test_unit_marked_and_unspaced_script_dates_match(
    text: str, expected: list[str]
) -> None:
    assert _dates(text) == expected


@pytest.mark.parametrize(
    "text",
    ["3月是春天。", "2024年13月5日", "3月32日", "第3月"],
)
def test_unit_marked_month_alone_or_invalid_is_not_a_date(text: str) -> None:
    assert _dates(text) == []


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Ödeme 3/15 : tarihinde yapıldı.", ["3/15"]),
        ("Ödeme 15.03:tarihinde yapıldı.", ["15.03"]),
        ("Ödeme 15.03    tarihinde yapıldı.", ["15.03"]),
        ("3/15:に入院した。", ["3/15"]),
        ("3/15 : 입원 예정", ["3/15"]),
    ],
)
def test_after_cue_matches_through_colon_and_extra_spaces(
    text: str, expected: list[str]
) -> None:
    assert _dates(text) == expected
