"""Mixed-language evaluation slice: fixture integrity, value placement and the per-segment-language leak metric."""

from __future__ import annotations

import json
import re
from collections import Counter

import pytest
from langdetect import DetectorFactory

from septum_api.utils.db_helpers import detect_language
from tests.benchmark_retrieval import (
    LEAK_TYPES,
    MIXED_LEAK_DOC_VALUES,
    MIXED_LEAK_ROUNDS,
    _LeakRecord,
    _mixed_filler,
    _mixed_leak_table,
    _mixed_leak_text,
    _pool,
    _segment_block,
    fill_mixed_document,
    segment_leak_report,
)
from tests.retrieval_eval import (
    ALLOWED_SLOT_TYPES,
    EVAL_DIR,
    SLOT_PATTERN,
    fixture_digest,
    fixture_files,
    list_slots,
    load_docs,
    load_mixed_docs,
    mixed_fixture_digest,
    mixed_fixture_files,
    split_segments,
)

LANGS = ("en", "tr", "zh")
LAYOUTS = Counter({"annex": 2, "section": 4, "interleaved": 1})
MIN_DOC_CHARS = 1500
MAX_DOC_CHARS = 10000
MIN_FILLER_CHARS = 150
MIN_MINORITY_VALUE_SHARE = 0.2
CROSS_PAIRS = {("tr", "en"), ("en", "zh"), ("zh", "en")}

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
LONG_DIGITS_RE = re.compile(r"\d{7,}")


@pytest.fixture(scope="module")
def mixed():
    return load_mixed_docs()


def test_seven_documents_with_expected_layouts(mixed):
    assert len(mixed) == 7
    assert Counter(d.layout for d in mixed) == LAYOUTS
    assert len({d.doc_id for d in mixed}) == 7
    for d in mixed:
        assert d.doc_id.startswith("mx_")
        assert d.path == f"mixed/{d.doc_id}.txt"
        assert d.abs_path.is_file()
        assert d.file_format == ("pdf" if d.structure == "structured" else "txt")


def test_main_and_minority_language_pairs(mixed):
    pairs = Counter((d.lang, next(l for l in d.segment_langs if l != d.lang)) for d in mixed if d.layout != "interleaved")
    assert pairs == Counter({pair: 2 for pair in CROSS_PAIRS})
    for d in mixed:
        if d.layout != "interleaved":
            assert len(set(d.segment_langs)) == 2


def test_segment_languages_are_valid_and_cover_every_block(mixed):
    for d in mixed:
        text = d.read_text()
        segments = split_segments(text, d.segment_langs)
        assert len(segments) == len(text.rstrip("\n").split("\n\n"))
        assert set(d.segment_langs) <= set(LANGS), d.doc_id
        assert d.lang in d.segment_langs, d.doc_id
        assert all(block.strip() for _, block in segments), d.doc_id


def test_split_segments_rejects_a_count_mismatch():
    with pytest.raises(ValueError):
        split_segments("a\n\nb", ["en"])


def test_blocks_are_in_one_language_by_script(mixed):
    han = re.compile(r"[一-鿿]")
    for d in mixed:
        for lang, block in d.segments():
            body = SLOT_PATTERN.sub("", block)
            share = len(han.findall(body)) / max(len(body), 1)
            if lang == "zh":
                assert share > 0.3, (d.doc_id, block[:40])
            else:
                assert share == 0, (d.doc_id, block[:40])


def test_interleaved_document_alternates_every_language(mixed):
    (doc,) = [d for d in mixed if d.layout == "interleaved"]
    body = doc.segment_langs[1:]
    assert set(body) == set(LANGS)
    assert all(a != b for a, b in zip(body, body[1:]))


def test_annex_documents_keep_the_minority_language_in_a_trailing_run(mixed):
    for d in mixed:
        if d.layout != "annex":
            continue
        minority = [i for i, l in enumerate(d.segment_langs) if l != d.lang]
        assert minority == list(range(minority[0], len(d.segment_langs)))


def test_length_is_within_bounds(mixed):
    for d in mixed:
        n = len(d.read_text())
        assert MIN_DOC_CHARS <= n <= MAX_DOC_CHARS, f"{d.doc_id}: {n} chars"


def test_no_literal_pii(mixed):
    for d in mixed:
        text = SLOT_PATTERN.sub("", d.read_text())
        assert EMAIL_RE.search(text) is None, d.doc_id
        assert LONG_DIGITS_RE.search(text) is None, d.doc_id


def test_slots_are_valid_contiguous_and_single_language(mixed):
    for d in mixed:
        text = d.read_text()
        assert "{{" not in SLOT_PATTERN.sub("", text) and "}}" not in SLOT_PATTERN.sub("", text)
        slots = list_slots(text)
        assert slots and {t for t, _ in slots} <= ALLOWED_SLOT_TYPES
        by_type: dict[str, list[int]] = {}
        for slot_type, index in slots:
            by_type.setdefault(slot_type, []).append(index)
        for slot_type, indexes in by_type.items():
            assert sorted(indexes) == list(range(1, len(indexes) + 1)), (d.doc_id, slot_type)
        langs_of: dict[tuple[str, int], set[str]] = {}
        for lang, block in d.segments():
            for slot in list_slots(block):
                langs_of.setdefault(slot, set()).add(lang)
        assert all(len(v) == 1 for v in langs_of.values()), d.doc_id


def test_every_declared_language_has_slot_free_filler(mixed):
    for d in mixed:
        filler, starts = _mixed_filler(d)
        bounds = [s for s, _ in starts] + [len(filler) + 2]
        sizes: Counter = Counter()
        for (_, lang), lo, hi in zip(starts, bounds, bounds[1:]):
            sizes[lang] += hi - lo - 2
        assert set(sizes) == set(d.segment_langs), d.doc_id
        assert all(n >= MIN_FILLER_CHARS for n in sizes.values()), (d.doc_id, dict(sizes))
        assert sum(sizes.values()) >= MIN_FILLER_CHARS * 5, d.doc_id


def test_mixed_slice_is_outside_the_baseline_digest(mixed):
    mixed_paths = {p.resolve() for p in mixed_fixture_files()}
    assert mixed_paths
    assert not mixed_paths & {p.resolve() for p in fixture_files()}
    assert all(d.doc_id.startswith("mx_") is False for d in load_docs())
    assert not (EVAL_DIR / "docs.jsonl").read_text(encoding="utf-8").count("mixed/")
    assert fixture_digest() != mixed_fixture_digest()


def test_mixed_digest_is_stable_and_hex():
    assert mixed_fixture_digest() == mixed_fixture_digest()
    assert re.fullmatch(r"[0-9a-f]{64}", mixed_fixture_digest())


def test_docs_jsonl_rows_have_the_documented_keys():
    rows = [json.loads(l) for l in (EVAL_DIR / "mixed" / "docs.jsonl").read_text(encoding="utf-8").splitlines() if l]
    for row in rows:
        assert set(row) == {
            "doc_id", "lang", "path", "structure", "domain", "file_format", "layout", "segment_langs",
        }


def test_filled_slots_come_from_the_pool_of_their_segment_language(mixed):
    for key, d in enumerate(mixed, start=1):
        template = d.read_text()
        filled, values = fill_mixed_document(key, template, d.segment_langs)
        assert "{{" not in filled
        slot_lang = {slot: lang for lang, block in d.segments() for slot in list_slots(block)}
        assert set(values) == set(slot_lang)
        for (slot_type, _), value in values.items():
            lang = slot_lang[(slot_type, _)]
            assert value in _pool(slot_type, lang), (d.doc_id, slot_type, lang, value)
        assert len(filled.rstrip("\n").split("\n\n")) == len(d.segment_langs)


def test_fill_rejects_a_slot_shared_between_languages():
    with pytest.raises(ValueError):
        fill_mixed_document(1, "{{PII:PERSON_NAME:1}}\n\n{{PII:PERSON_NAME:1}}", ["en", "tr"])


def test_leak_values_use_the_pool_of_the_segment_they_follow(mixed):
    seen: Counter = Counter()
    for d in mixed:
        text, placed = _mixed_leak_text(d, 120, 0)
        assert len(placed) == 120
        for slot_type, value, start, end, lang in placed:
            assert slot_type in LEAK_TYPES
            assert text[start:end] == value
            assert value in _pool(slot_type, lang), (d.doc_id, slot_type, lang, value)
            assert lang in d.segment_langs
            seen[(d.doc_id, lang)] += 1
        assert {lang for (doc, lang) in seen if doc == d.doc_id} == set(d.segment_langs), d.doc_id


def test_leak_value_language_matches_the_filler_it_follows(mixed):
    han = re.compile(r"[一-鿿]")
    for d in mixed:
        text, placed = _mixed_leak_text(d, 120, 1)
        for _, _, start, _, lang in placed:
            window = text[max(start - 40, 0) : start].strip()
            if lang == "zh":
                assert han.search(window), (d.doc_id, window)
            else:
                assert not han.search(window[-3:]), (d.doc_id, window)


def test_leak_values_never_split_a_word(mixed):
    for d in mixed:
        text, placed = _mixed_leak_text(d, 120, 2)
        for _, _, start, _, _ in placed:
            assert start > 0
            assert text[start - 1] == " "


def test_leak_text_is_deterministic_and_parts_differ(mixed):
    d = mixed[0]
    assert _mixed_leak_text(d, 60, 0) == _mixed_leak_text(d, 60, 0)
    assert _mixed_leak_text(d, 60, 0) != _mixed_leak_text(d, 60, 1)


def _record(lang: str, doc_lang: str, *, literal=False, fragment=False, scorable=True, kind="control", slot_type="PERSON_NAME"):
    return _LeakRecord(slot_type, kind, literal, fragment, scorable, False, lang, doc_lang)


def test_segment_leak_report_groups_by_segment_language():
    records = [
        _record("tr", "tr", literal=False),
        _record("tr", "tr", literal=False),
        _record("en", "tr", literal=True, fragment=True),
        _record("en", "tr", literal=False, fragment=True),
        _record("en", "tr", literal=False, fragment=False),
        _record("zh", "tr", literal=True, scorable=False),
    ]
    report = segment_leak_report(records)
    by_lang = report["by_segment_lang"]
    assert list(by_lang) == ["en", "tr", "zh"]
    assert by_lang["tr"]["values"] == 2
    assert by_lang["tr"]["literal"] == {"n": 2, "leaks": 0, "rate": 0.0}
    assert by_lang["en"]["literal"] == {"n": 3, "leaks": 1, "rate": pytest.approx(1 / 3)}
    assert by_lang["en"]["fragment"] == {"n": 3, "leaks": 2, "rate": pytest.approx(2 / 3)}
    assert by_lang["zh"]["literal"]["leaks"] == 1
    assert by_lang["zh"]["fragment"] == {"n": 0, "leaks": 0, "rate": None}


def test_segment_leak_report_splits_matched_and_mismatched():
    records = [
        _record("tr", "tr", literal=False),
        _record("en", "tr", literal=True),
        _record("zh", "tr", literal=True),
        _record("zh", "tr", literal=False),
    ]
    match = segment_leak_report(records)["by_detection_match"]
    assert match["matched"]["values"] == 1 and match["matched"]["literal"]["leaks"] == 0
    assert match["mismatched"]["values"] == 3
    assert match["mismatched"]["literal"] == {"n": 3, "leaks": 2, "rate": pytest.approx(2 / 3)}


def test_segment_leak_report_breaks_out_types_and_arms():
    records = [
        _record("en", "tr", literal=True, slot_type="EMAIL_ADDRESS", kind="crossing"),
        _record("en", "tr", literal=False, slot_type="EMAIL_ADDRESS", kind="control"),
        _record("en", "tr", literal=False, slot_type="PERSON_NAME", kind="control"),
    ]
    block = segment_leak_report(records)["by_segment_lang"]["en"]
    assert block["by_type"]["EMAIL_ADDRESS"]["literal"] == {"n": 2, "leaks": 1, "rate": 0.5}
    assert block["by_type"]["IBAN"]["literal"]["rate"] is None
    assert block["arms"]["crossing"]["literal"]["leaks"] == 1
    assert block["arms"]["control"]["literal"]["n"] == 2


def test_segment_leak_report_of_nothing_is_empty():
    report = segment_leak_report([])
    assert report["by_segment_lang"] == {}
    assert report["by_detection_match"]["matched"]["values"] == 0
    assert report["by_detection_match"]["mismatched"]["literal"]["rate"] is None


def test_sectioned_documents_are_detected_as_their_main_language(mixed):
    """The leak text of an annex or section document is masked with its main language, as labelled.

    The minority language still carries a fair share of the values, so its segments are measured.
    """
    DetectorFactory.seed = 42
    for d in mixed:
        if d.layout == "interleaved":
            continue
        for part in range(MIXED_LEAK_ROUNDS):
            text, placed = _mixed_leak_text(d, MIXED_LEAK_DOC_VALUES, part)
            assert detect_language(text, fallback="en") == d.lang, (d.doc_id, part)
            minority = sum(1 for *_, lang in placed if lang != d.lang)
            assert minority >= MIN_MINORITY_VALUE_SHARE * len(placed), (d.doc_id, part, minority)


def test_segment_leak_report_splits_each_language_by_detection_match():
    records = [
        _record("en", "en", literal=True),
        _record("en", "en", literal=False),
        _record("en", "tr", literal=False),
        _record("en", "zh", literal=True),
        _record("en", "zh", literal=True),
        _record("tr", "tr", literal=False),
    ]
    split = segment_leak_report(records)["by_segment_lang_match"]
    assert list(split) == ["en", "tr"]
    assert split["en"]["matched"]["literal"] == {"n": 2, "leaks": 1, "rate": 0.5}
    assert split["en"]["mismatched"]["literal"] == {"n": 3, "leaks": 2, "rate": pytest.approx(2 / 3)}
    assert split["en"]["mismatched"]["by_type"]["PERSON_NAME"]["literal"]["n"] == 3
    assert split["tr"]["matched"]["values"] == 1
    assert split["tr"]["mismatched"] == _segment_block([])


def test_mixed_leak_table_shows_matched_mismatched_and_reference_per_language():
    records = [_record("en", "en", literal=True), _record("en", "tr"), _record("tr", "tr")]
    mixed = {
        **segment_leak_report(records),
        "single_language_reference": {"en": _segment_block([_record("en", "en")])},
    }
    rows = {line.split(" | ")[0]: line for line in _mixed_leak_table(mixed) if line.startswith("| en |")
            or line.startswith("| tr |")}
    assert rows["| en"].endswith("| 1.000 (1/1) / 0.000 (0/1) | 0.000 (0/1) / 0.000 (0/1) | 0.000 (0/1) / 0.000 (0/1) |")
    assert rows["| tr"].endswith("| 0.000 (0/1) / 0.000 (0/1) | - (0/0) / - (0/0) | - |")
