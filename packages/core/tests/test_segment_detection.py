"""Per-segment detection of a mixed-language document (``Detector.detect(segments=)``).

NER is stubbed per model id: each fake model finds only the names it is told
to, so a name is found exactly when a model that knows it ran on its segment.
"""

from __future__ import annotations

import unicodedata

import pytest
from ner_fakes import FakePipeline
from septum_core.config import SeptumCoreConfig
from septum_core.detector import Detector
from septum_core.document_windowing import WindowingPolicy, plan_padded_windows
from septum_core.language_segments import (
    Segment,
    SegmentLookup,
    clip_segments,
    rebase_segments,
    validate_segments,
)
from septum_core.ner_model_registry import (
    MAX_EXTRA_NER_MODELS,
    NERModelRegistry,
    _WindowedNERPipeline,
)

_NER_ONLY = SeptumCoreConfig(use_presidio_layer=False, use_ner_layer=True)
_SMALL = WindowingPolicy(max_whole_chars=300, window_chars=1000, overlap_chars=100)

EN_FILLER = "The committee reviewed the annual budget in detail. "
TR_FILLER = "Komite yıllık bütçeyi ayrıntılı olarak inceledi. "
BASE = NERModelRegistry().model_ids("en")
TR_IDS = NERModelRegistry().model_ids("tr")
FALLBACK_IDS = NERModelRegistry().model_ids("xx")


class FakeRegistry(NERModelRegistry):
    """Real model-set planning, stub pipelines; records every model-id set asked for."""

    def __init__(self, emits: dict[str, dict[str, str]], overrides: dict[str, str] | None = None):
        super().__init__(_overrides=overrides or {})
        self._emits = emits
        self.requested: list[tuple[str, ...]] = []
        self.fakes: list[list[FakePipeline]] = []

    def get_pipelines_for_models(self, model_ids):
        ids = tuple(model_ids)
        self.requested.append(ids)
        fakes = [FakePipeline(emit=self._emits.get(mid, {})) for mid in ids]
        self.fakes.append(fakes)
        return [_WindowedNERPipeline(fake, None) for fake in fakes]

    def chunks_of_call(self, index: int) -> list[str]:
        return [chunk for fake in self.fakes[index] for chunk in fake.chunks]

    @property
    def loaded_ids(self) -> set[str]:
        return {mid for ids in self.requested for mid in ids}


def make_detector(emits, overrides=None, config=_NER_ONLY) -> tuple[Detector, FakeRegistry]:
    registry = FakeRegistry(emits, overrides)
    return Detector(config, ner_registry=registry), registry


def covered(result, text: str, needle: str, occurrence: int = 0) -> bool:
    start = -1
    for _ in range(occurrence + 1):
        start = text.index(needle, start + 1)
    return any(s.start <= start and s.end >= start + len(needle) for s in result.spans)


def two_part_text(en_name="Brown Smith", tr_name="Ayse Demir"):
    en = EN_FILLER * 10 + f"Mr {en_name} attended the meeting. " + EN_FILLER * 4
    tr = TR_FILLER * 10 + f"Sayin {tr_name} toplantiya katildi. " + TR_FILLER * 4
    return en + tr, len(en)


EMITS = {BASE[0]: {"Brown Smith": "PER"}, TR_IDS[0]: {"Ayse Demir": "PER"}}


def test_minority_name_found_with_segments_missed_without() -> None:
    text, cut = two_part_text()
    segments = [Segment(0, cut, "en"), Segment(cut, len(text), "tr")]
    detector, _ = make_detector(EMITS)

    today = detector.detect(text, "en")
    mixed = detector.detect(text, "en", segments=segments)

    assert covered(today, text, "Brown Smith") and not covered(today, text, "Ayse Demir")
    assert covered(mixed, text, "Brown Smith") and covered(mixed, text, "Ayse Demir")


def test_dominant_set_reads_the_whole_text_and_a_minority_segment_adds_only_its_extra_models() -> None:
    text, cut = two_part_text()
    detector, registry = make_detector(EMITS)

    detector.detect(text, "en", segments=[Segment(0, cut, "en"), Segment(cut, len(text), "tr")])

    assert registry.requested == [tuple(BASE), tuple(TR_IDS), tuple(TR_IDS)]
    assert registry.chunks_of_call(0) == [text]
    (window,) = set(registry.chunks_of_call(1))
    assert text.endswith(window) and 0 < len(text) - len(window) < cut


def test_an_edge_between_different_model_sets_is_scored_with_the_extra_models() -> None:
    text, cut = two_part_text()
    detector, registry = make_detector(EMITS)

    detector.detect(text, "en", segments=[Segment(0, cut, "en"), Segment(cut, len(text), "tr")])

    chunks = set(registry.chunks_of_call(2))
    assert len(chunks) == 1
    (edge,) = chunks
    assert text[cut - 100 : cut + 100] in edge
    assert 900 <= len(edge) <= 1100


def test_neighbouring_segments_with_one_model_set_have_no_edge_window() -> None:
    text, cut = two_part_text()
    detector, registry = make_detector({})

    detector.detect(text, "en", segments=[Segment(0, cut, "en"), Segment(cut, len(text), "es")])

    assert registry.requested == [tuple(BASE)]


def test_mislabelled_segment_still_gets_the_dominant_models() -> None:
    text = EN_FILLER * 14 + "Mr Brown Smith attended. " + EN_FILLER * 14
    cut = len(EN_FILLER * 14)
    detector, registry = make_detector(EMITS)

    result = detector.detect(
        text, "en", segments=[Segment(0, cut - 100, "en"), Segment(cut - 100, len(text), "tr")]
    )

    assert covered(result, text, "Brown Smith")
    assert registry.requested[0] == tuple(BASE)
    assert registry.chunks_of_call(0) == [text]
    assert all(not set(BASE) & set(ids) for ids in registry.requested[1:])


def test_model_cap_sends_the_overflow_to_the_fallback_set() -> None:
    cap = MAX_EXTRA_NER_MODELS
    assert cap == 3
    overrides = {"pl": "x/pl-a,x/pl-b", "ru": "x/ru-a"}
    emits = {
        **EMITS,
        "x/pl-a": {"Jan Kowalski": "PER"},
        FALLBACK_IDS[0]: {"Ivan Petrov": "PER"},
    }
    tr = TR_FILLER * 30
    pl = "Spotkanie komitetu odbylo sie w poniedzialek. " * 8 + "Jan Kowalski wyszedl. "
    ru = "Sobranie komiteta proshlo v ponedelnik. " * 8 + "Ivan Petrov ushel. "
    en = EN_FILLER * 6
    text = en + tr + pl + ru
    bounds = [0, len(en), len(en + tr), len(en + tr + pl), len(text)]
    segments = [
        Segment(a, b, lang) for (a, b), lang in zip(zip(bounds, bounds[1:]), ("en", "tr", "pl", "ru"))
    ]
    detector, registry = make_detector(emits, overrides)

    result = detector.detect(text, "en", segments=segments)

    extra = registry.loaded_ids - set(BASE)
    assert extra == {*TR_IDS, FALLBACK_IDS[0]}
    assert len(extra) <= cap
    assert not covered(result, text, "Jan Kowalski")
    assert covered(result, text, "Ivan Petrov")


def test_plan_counts_extra_models_over_the_whole_document() -> None:
    registry = NERModelRegistry(
        _overrides={"pl": "x/a,x/b", "ru": "x/c", "cs": "x/d", "sk": "x/e"}
    )

    plan = registry.plan_segment_models("en", ["tr", "pl", "ru", "cs", "sk"])

    base = tuple(BASE)
    mbert = FALLBACK_IDS[0]
    assert plan["en"] == base
    assert plan["tr"] == (*base, *TR_IDS)
    assert plan["pl"] == (*base, mbert)
    assert plan["ru"] == (*base, mbert)
    used = {m for ids in plan.values() for m in ids} - set(base)
    assert len(used) == 3


def test_plan_falls_back_to_the_dominant_set_when_even_the_fallback_does_not_fit() -> None:
    registry = NERModelRegistry(_overrides={"pl": "x/a", "cs": "x/b", "ru": "x/c"})

    plan = registry.plan_segment_models("en", ["tr", "pl", "cs", "ru"])

    assert plan["tr"] == (*BASE, *TR_IDS)
    assert plan["pl"] == (*BASE, "x/a")
    assert plan["cs"] == tuple(BASE)
    assert plan["ru"] == tuple(BASE)


def test_plan_gives_a_language_inside_the_dominant_set_nothing_extra() -> None:
    plan = NERModelRegistry().plan_segment_models("en", ["es", "fr"])
    assert plan == {"en": tuple(BASE), "es": tuple(BASE), "fr": tuple(BASE)}


def test_languages_covering_more_text_keep_their_own_models_at_the_cap() -> None:
    small = "Sobranie komiteta proshlo. " * 6
    tr = TR_FILLER * 40
    en = EN_FILLER * 4
    text = en + small + tr
    segments = [
        Segment(0, len(en), "en"),
        Segment(len(en), len(en + small), "ru"),
        Segment(len(en + small), len(text), "tr"),
    ]
    detector, registry = make_detector({}, {"ru": "x/r1,x/r2"})

    detector.detect(text, "en", segments=segments)

    assert set(TR_IDS) <= registry.loaded_ids
    assert not {"x/r1", "x/r2"} & registry.loaded_ids


def test_ner_span_floor_follows_the_segment_language() -> None:
    en = EN_FILLER * 12 + "Please contact Qi about the invoice. " + EN_FILLER * 4
    zh = TR_FILLER * 12 + "Please contact Qi about the invoice. " + TR_FILLER * 4
    text = en + zh
    detector, _ = make_detector({BASE[0]: {"Qi": "PER"}})
    segments = [Segment(0, len(en), "en"), Segment(len(en), len(text), "zh")]

    result = detector.detect(text, "en", segments=segments)

    assert not covered(result, text, "Qi", occurrence=0)
    assert covered(result, text, "Qi", occurrence=1)
    assert not covered(detector.detect(text, "en"), text, "Qi", occurrence=1)


ZH_BASE = tuple(NERModelRegistry().model_ids("zh"))


def segmented(first: str, second: str, languages=("zh", "en")):
    """``first`` + ``second`` with one segment each, in ``languages``."""
    text = first + second
    return text, [Segment(0, len(first), languages[0]), Segment(len(first), len(text), languages[1])]


class DropsByLanguage:
    """Non-PII filter that drops every span when judged in one of ``languages``."""

    def __init__(self, languages) -> None:
        self.languages = set(languages)

    def filter_spans(self, text, language, spans):
        return [] if language in self.languages else list(spans)


def test_short_ner_span_of_the_dominant_floor_survives_a_higher_segment_floor() -> None:
    first = EN_FILLER * 12
    second = "Please contact Qi about the invoice. " + EN_FILLER * 4
    text, segments = segmented(first, second)
    detector, _ = make_detector({ZH_BASE[0]: {"Qi": "PER"}})

    assert covered(detector.detect(text, "zh"), text, "Qi")
    assert covered(detector.detect(text, "zh", segments=segments), text, "Qi")


@pytest.mark.parametrize(
    ("dropping", "keeps_without_segments"),
    [({"en"}, True), ({"zh"}, False)],
)
def test_non_pii_filter_drops_a_span_only_when_both_languages_would(
    dropping, keeps_without_segments
) -> None:
    first = EN_FILLER * 12
    second = "Mr Brown Smith attended. " + EN_FILLER * 4
    text, segments = segmented(first, second)
    detector, _ = make_detector({ZH_BASE[0]: {"Brown Smith": "PER"}})
    detector._non_pii_filter = DropsByLanguage(dropping)

    assert covered(detector.detect(text, "zh"), text, "Brown Smith") is keeps_without_segments
    assert covered(detector.detect(text, "zh", segments=segments), text, "Brown Smith")


def test_policy_label_span_kept_by_the_dominant_language_is_kept_with_segments() -> None:
    first = EN_FILLER * 12
    second = "Ref IBAN\u2019\u0131n shown here. " + EN_FILLER * 4
    text, segments = segmented(first, second, ("en", "tr"))
    detector, _ = make_detector({BASE[0]: {"IBAN\u2019\u0131n": "PER"}})
    detector._label_terms = ("IBAN",)

    assert covered(detector.detect(text, "en"), text, "IBAN")
    assert covered(detector.detect(text, "en", segments=segments), text, "IBAN")


def test_policy_label_span_kept_only_by_the_segment_language_is_kept_with_segments() -> None:
    """The dominant language strips the possessive and reads a label; the segment language does not."""
    first = TR_FILLER * 12
    second = "Ref IBAN’ın shown here. " + EN_FILLER * 4
    text, segments = segmented(first, second, ("tr", "en"))
    detector, _ = make_detector({TR_IDS[0]: {"IBAN’ın": "PER"}})
    detector._label_terms = ("IBAN",)

    assert not covered(detector.detect(text, "tr"), text, "IBAN")
    assert covered(detector.detect(text, "tr", segments=segments), text, "IBAN")


def test_policy_label_span_dropped_by_both_languages_is_dropped_with_segments() -> None:
    first = EN_FILLER * 12
    second = "Ref IBAN shown here. " + EN_FILLER * 4
    text, segments = segmented(first, second, ("en", "tr"))
    detector, _ = make_detector({BASE[0]: {"IBAN": "PER"}})
    detector._label_terms = ("IBAN",)

    assert not covered(detector.detect(text, "en"), text, "IBAN")
    assert not covered(detector.detect(text, "en", segments=segments), text, "IBAN")


def _stopword_case(dominant: str, minority: str):
    """``\u0130N`` alone in a minority segment: it folds to the stopword "in" only under dotted-I casing (tr).

    The words around it are lower case, so the span is not grown into a
    longer name and the stopword rule really judges ``\u0130N`` itself.
    """
    first = EN_FILLER * 12
    second = "please call \u0130N now. " + EN_FILLER * 4
    text, segments = segmented(first, second, (dominant, minority))
    dominant_ids = NERModelRegistry().model_ids(dominant)
    detector, _ = make_detector({dominant_ids[0]: {"\u0130N": "PER"}})
    return text, segments, detector


def test_stopword_span_of_the_dominant_language_survives_a_segment_that_reads_it_as_one() -> None:
    text, segments, detector = _stopword_case("zh", "tr")

    without = detector.detect(text, "zh")
    assert [text[s.start : s.end] for s in without.spans] == ["\u0130N"]
    assert covered(detector.detect(text, "zh", segments=segments), text, "\u0130N")


def test_stopword_span_of_the_dominant_language_kept_by_its_segment_language_is_kept() -> None:
    text, segments, detector = _stopword_case("tr", "zh")

    assert not covered(detector.detect(text, "tr"), text, "\u0130N")
    assert covered(detector.detect(text, "tr", segments=segments), text, "\u0130N")


def test_presidio_runs_once_over_the_whole_text(monkeypatch) -> None:
    text, cut = two_part_text()
    detector, _ = make_detector(
        EMITS, config=SeptumCoreConfig(use_presidio_layer=True, use_ner_layer=True)
    )
    calls: list[int] = []
    real = detector._presidio_spans

    def counting(normalized_text):
        calls.append(len(normalized_text))
        return real(normalized_text)

    monkeypatch.setattr(detector, "_presidio_spans", counting)

    detector.detect(text, "en", segments=[Segment(0, cut, "en"), Segment(cut, len(text), "tr")])

    assert calls == [len(text)]


def test_single_segment_in_the_call_language_is_byte_identical() -> None:
    text, _ = two_part_text()
    text += " Mail jane.doe@example.com now."
    detector, registry = make_detector(
        EMITS, config=SeptumCoreConfig(use_presidio_layer=True, use_ner_layer=True)
    )

    plain = detector.detect(text, "en")
    asked = len(registry.requested)
    for segments in (
        [Segment(0, len(text), "en")],
        [Segment(0, len(text), "en-GB")],
        [Segment(0, 30, "en"), Segment(30, len(text), "en")],
    ):
        assert detector.detect(text, "en", segments=segments) == plain
    assert registry.requested[asked:] == registry.requested[:asked] * 3
    assert plain.spans


def test_single_segment_is_byte_identical_on_the_windowed_path() -> None:
    text, _ = two_part_text()
    text += " Mail jane.doe@example.com now."
    detector, _ = make_detector(
        EMITS, config=SeptumCoreConfig(use_presidio_layer=True, use_ner_layer=True)
    )
    hints = [i for i, ch in enumerate(text) if ch == "."]

    plain = detector.detect(text, "en", boundaries=hints, windowing=_SMALL)
    single = detector.detect(
        text, "en", boundaries=hints, windowing=_SMALL, segments=[Segment(0, len(text), "en")]
    )

    assert single == plain
    assert plain.spans


def test_windowed_path_finds_the_minority_name_only_with_segments() -> None:
    text, cut = two_part_text()
    segments = [Segment(0, cut, "en"), Segment(cut, len(text), "tr")]
    detector, registry = make_detector(EMITS)

    today = detector.detect(text, "en", windowing=_SMALL)
    registry.requested.clear()
    mixed = detector.detect(text, "en", windowing=_SMALL, segments=segments)

    assert not covered(today, text, "Ayse Demir")
    assert covered(mixed, text, "Brown Smith") and covered(mixed, text, "Ayse Demir")
    assert tuple(BASE) in registry.requested
    assert tuple(TR_IDS) in registry.requested


def test_windows_inside_the_dominant_language_run_the_dominant_set_only() -> None:
    en = EN_FILLER * 40
    text = en + TR_FILLER * 6
    detector, registry = make_detector(EMITS)

    detector.detect(
        text, "en", windowing=_SMALL, segments=[Segment(0, len(en), "en"), Segment(len(en), len(text), "tr")]
    )

    assert registry.requested[0] == tuple(BASE)
    assert registry.requested.count(tuple(BASE)) >= 2


def test_segments_follow_the_text_through_unicode_normalization() -> None:
    en = EN_FILLER * 12 + "Mr Brown Smith attended. " + EN_FILLER * 4
    tr = TR_FILLER * 12 + "Sayin Ayşe Demir toplantiya katildi. " + TR_FILLER * 4
    decomposed = unicodedata.normalize("NFD", en + tr)
    cut = len(unicodedata.normalize("NFD", en))
    emits = {BASE[0]: {"Brown Smith": "PER"}, TR_IDS[0]: {"Ayşe Demir": "PER"}}
    detector, _ = make_detector(emits)

    result = detector.detect(
        decomposed, "en", segments=[Segment(0, cut, "en"), Segment(cut, len(decomposed), "tr")]
    )

    assert covered(result, result.text, "Ayşe Demir")
    assert len(result.text) < len(decomposed)


@pytest.mark.parametrize(
    "segments",
    [
        [Segment(0, 10, "en")],
        [Segment(1, 100, "en")],
        [Segment(0, 40, "en"), Segment(50, 100, "tr")],
        [Segment(0, 60, "en"), Segment(40, 100, "tr")],
        [Segment(0, 0, "en"), Segment(0, 100, "tr")],
        [Segment(0, 100, "not a code!")],
        [],
    ],
)
def test_segments_that_do_not_tile_the_text_are_rejected(segments) -> None:
    detector, _ = make_detector({})
    with pytest.raises(ValueError):
        detector.detect("a" * 100, "en", segments=segments)


def test_empty_text_with_no_segments_is_empty() -> None:
    detector, _ = make_detector({})
    assert detector.detect("", "en", segments=()).spans == ()


def test_ner_off_still_applies_per_segment_post_processing() -> None:
    text, cut = two_part_text()
    detector, registry = make_detector(
        EMITS, config=SeptumCoreConfig(use_presidio_layer=False, use_ner_layer=False)
    )

    result = detector.detect(
        text, "en", segments=[Segment(0, cut, "en"), Segment(cut, len(text), "tr")]
    )

    assert result.spans == () and registry.requested == []


def test_validate_segments_canonicalises_languages() -> None:
    assert validate_segments([Segment(0, 5, "ZH_cn")], 5) == (Segment(0, 5, "zh"),)


def test_rebase_segments_follows_normalization_length_change() -> None:
    text = unicodedata.normalize("NFD", "çok güzel") + "\n" + unicodedata.normalize("NFD", "şehir")
    cut = text.index("\n") + 1
    rebased = rebase_segments([Segment(0, cut, "tr"), Segment(cut, len(text), "en")], text)
    normalized = unicodedata.normalize("NFC", text)
    assert rebased[0] == Segment(0, len(unicodedata.normalize("NFC", text[:cut])), "tr")
    assert rebased[-1].end == len(normalized)
    assert rebased[0].end == rebased[1].start


def test_rebase_segments_keeps_offsets_of_normalized_text() -> None:
    segments = (Segment(0, 3, "en"), Segment(3, 9, "tr"))
    assert rebase_segments(segments, "abcdefghi") == segments


def test_clip_segments_shifts_to_the_window() -> None:
    segments = [Segment(0, 10, "en"), Segment(10, 30, "tr"), Segment(30, 40, "en")]
    assert clip_segments(segments, 5, 35) == (
        Segment(0, 5, "en"),
        Segment(5, 25, "tr"),
        Segment(25, 30, "en"),
    )
    assert clip_segments(segments, 10, 30) == (Segment(0, 20, "tr"),)


def test_segment_lookup_clamps_and_bisects() -> None:
    lookup = SegmentLookup([Segment(0, 10, "en"), Segment(10, 20, "tr")])
    assert [lookup.language_at(p) for p in (-1, 0, 9, 10, 19, 99)] == [
        "en", "en", "en", "tr", "tr", "tr",
    ]


def test_padded_windows_widen_each_range_and_keep_it_inside() -> None:
    text = "word " * 400
    windows = plan_padded_windows(text, [(0, 600), (600, 1500), (1500, 2000)], 200)
    assert [(w.first, w.last) for w in windows] == [(True, False), (False, False), (False, True)]
    for (start, end), window in zip([(0, 600), (600, 1500), (1500, 2000)], windows):
        assert window.start <= start and window.end >= end
        assert window.start >= max(0, start - 200) - 50
        assert window.end <= min(len(text), end + 200)
    assert text[windows[1].start - 1] == " " and text[windows[1].end - 1] == " "


def test_a_name_crossing_a_segment_cut_that_only_the_minority_model_finds_is_covered_whole() -> None:
    first = EN_FILLER * 12 + "Sayin Ayse "
    second = "Demir toplantiya katildi. " + TR_FILLER * 6
    text, segments = segmented(first, second, ("en", "tr"))
    detector, _ = make_detector({TR_IDS[0]: {"Ayse Demir": "PER"}})

    assert not covered(detector.detect(text, "en"), text, "Ayse Demir")
    result = detector.detect(text, "en", segments=segments)

    assert covered(result, text, "Ayse Demir")
    assert not any(s.end > text.index("Ayse Demir") + len("Ayse Demir") + 1 for s in result.spans)


def nfd_text(words: int) -> str:
    return unicodedata.normalize("NFD", "cafe\u0301 " * words)


def test_segment_offsets_are_rebased_when_normalization_shortens_the_text_before_the_cut() -> None:
    first = nfd_text(900)
    second = "Sayin Ayse Demir toplantiya katildi. " + TR_FILLER * 6
    text = first + second
    assert len(unicodedata.normalize("NFC", first)) <= len(first) - 900
    detector, _ = make_detector({TR_IDS[0]: {"Ayse Demir": "PER"}})

    result = detector.detect(
        text, "en", segments=[Segment(0, len(first), "en"), Segment(len(first), len(text), "tr")]
    )

    assert covered(result, result.text, "Ayse Demir")


def test_segments_follow_normalization_on_the_windowed_path() -> None:
    first = nfd_text(900)
    second = "Sayin Ayse Demir toplantiya katildi. " + TR_FILLER * 6
    text = first + second
    detector, _ = make_detector({TR_IDS[0]: {"Ayse Demir": "PER"}})
    policy = WindowingPolicy(max_whole_chars=300, window_chars=1200, overlap_chars=100)

    result = detector.detect(
        text,
        "en",
        windowing=policy,
        segments=[Segment(0, len(first), "en"), Segment(len(first), len(text), "tr")],
    )

    assert covered(result, result.text, "Ayse Demir")


def test_a_cut_between_a_letter_and_its_combining_mark_never_inverts_the_segments() -> None:
    text = unicodedata.normalize("NFD", "caf\u00e9 " * 40)
    cut = text.index("\u0301")
    rebased = rebase_segments([Segment(0, cut, "en"), Segment(cut, len(text), "tr")], text)
    normalized = unicodedata.normalize("NFC", text)

    assert rebased[0].start == 0 and rebased[-1].end == len(normalized)
    assert all(a.end == b.start for a, b in zip(rebased, rebased[1:]))
    assert all(s.start < s.end for s in rebased)
    for segment in rebased:
        assert normalized[segment.start : segment.end].isalpha() or " " in normalized


def _flat_words(count: int) -> str:
    return "abc " * count


@pytest.mark.parametrize("which", ["window_end", "window_start", "whole_text_limit"])
def test_a_segment_edge_on_a_window_cut_keeps_a_name_across_it_covered(which) -> None:
    from septum_core.document_windowing import plan_text_windows

    text = _flat_words(750)
    name = "Ayse Demir"
    cut = None
    for _ in range(4):
        windows = plan_text_windows(text, _SMALL)
        cut = {
            "window_end": windows[0].end,
            "window_start": windows[1].start,
            "whole_text_limit": _SMALL.max_whole_chars,
        }[which]
        placed = text[: cut - 5] + name + text[cut + 5 :]
        if placed == text:
            break
        text = placed
    detector, _ = make_detector({TR_IDS[0]: {name: "PER"}})
    assert text[cut - 5 : cut + 5] == name

    result = detector.detect(
        text, "en", windowing=_SMALL, segments=[Segment(0, cut, "en"), Segment(cut, len(text), "tr")]
    )

    assert covered(result, text, name)


def test_more_segments_than_the_limit_detect_the_document_in_one_language(caplog) -> None:
    from septum_core.detector import _MAX_SEGMENTS_PER_DOCUMENT

    text = "Mr Brown Smith attended. " + EN_FILLER * 80
    count = _MAX_SEGMENTS_PER_DOCUMENT + 1
    size = len(text) // count
    edges = [i * size for i in range(count)] + [len(text)]
    segments = [
        Segment(a, b, "tr" if i % 2 else "en") for i, (a, b) in enumerate(zip(edges, edges[1:]))
    ]
    detector, registry = make_detector(EMITS)

    with caplog.at_level("WARNING"):
        result = detector.detect(text, "en", segments=segments)

    assert result == detector.detect(text, "en")
    assert registry.requested[0] == tuple(BASE)
    assert set(registry.requested) == {tuple(BASE)}
    assert str(count) in caplog.text


def test_exactly_the_segment_limit_is_still_detected_per_segment() -> None:
    from septum_core.detector import _MAX_SEGMENTS_PER_DOCUMENT

    text = "Mr Brown Smith attended. " + EN_FILLER * 80
    count = _MAX_SEGMENTS_PER_DOCUMENT
    size = len(text) // count
    edges = [i * size for i in range(count)] + [len(text)]
    segments = [
        Segment(a, b, "tr" if i % 2 else "en") for i, (a, b) in enumerate(zip(edges, edges[1:]))
    ]
    detector, _ = make_detector(EMITS)

    assert detector.detect(text, "en", segments=segments).segment_languages == ("tr",)


def test_plan_entries_start_with_the_whole_dominant_set_of_a_two_model_override() -> None:
    registry = NERModelRegistry(_overrides={"en": "x/a,x/b"})

    plan = registry.plan_segment_models("en", ["tr", "zh", "de"])

    assert plan["en"] == ("x/a", "x/b")
    assert all(ids[:2] == ("x/a", "x/b") for ids in plan.values())
    assert len(plan["tr"]) == 4


def test_a_language_with_no_model_and_no_fallback_runs_the_dominant_set() -> None:
    registry = NERModelRegistry()
    del registry.DEFAULT_MODEL_MAP["fallback"]

    plan = registry.plan_segment_models("en", ["qq", "tr"])

    assert plan["qq"] == tuple(BASE)
    assert plan["tr"] == (*BASE, *TR_IDS)


def _distant_mark_text(units: int) -> str:
    """Each unit: a letter, 100 marks, then a mark that composes with the distant letter."""
    return ("e" + "\u0316" * 100 + "\u0301 ") * units


def _distant_mark_segments(text: str) -> list[Segment]:
    cuts = [0] + [i for i, ch in enumerate(text) if ch == "\u0301"] + [len(text)]
    return [
        Segment(a, b, "tr" if i % 2 else "en")
        for i, (a, b) in enumerate(zip(cuts, cuts[1:]))
    ]


def test_segments_that_cannot_be_mapped_onto_non_stream_safe_text_are_refused() -> None:
    text = _distant_mark_text(121)
    segments = _distant_mark_segments(text)
    assert len(segments) == 122
    assert rebase_segments(segments, text) is None


def test_detect_ignores_segments_it_cannot_map_onto_the_normalized_text(caplog) -> None:
    text = "Mr Brown Smith attended. " + _distant_mark_text(60) + TR_FILLER * 3
    segments = _distant_mark_segments(text)
    detector, registry = make_detector(EMITS)

    with caplog.at_level("WARNING"):
        result = detector.detect(text, "en", segments=segments)

    assert result == detector.detect(text, "en")
    assert set(registry.requested) == {tuple(BASE)}
    assert "cannot be mapped" in caplog.text


def _tiled(text: str, cuts: list[int], languages: list[str]) -> list[Segment]:
    edges = [0, *cuts, len(text)]
    return [Segment(a, b, lang) for (a, b), lang in zip(zip(edges, edges[1:]), languages)]


def test_result_reports_the_other_languages_it_applied_by_share_of_text() -> None:
    en = EN_FILLER * 6
    tr = TR_FILLER * 30
    de = "Der Ausschuss pruefte den Haushalt ausfuehrlich. " * 8
    text = en + de + tr
    detector, _ = make_detector({})

    result = detector.detect(
        text, "en", segments=_tiled(text, [len(en), len(en + de)], ["en", "de", "tr"])
    )

    assert result.segment_languages == ("tr", "de")


def test_result_reports_a_regional_tag_by_its_canonical_code() -> None:
    text, cut = two_part_text()
    detector, _ = make_detector({})

    result = detector.detect(
        text, "en", segments=[Segment(0, cut, "en-GB"), Segment(cut, len(text), "tr-TR")]
    )

    assert result.segment_languages == ("tr",)


@pytest.mark.parametrize("segments", [None, "single", "all_dominant"])
def test_result_reports_no_other_language_without_a_mixed_segment_list(segments) -> None:
    text, cut = two_part_text()
    given = {
        None: None,
        "single": [Segment(0, len(text), "en")],
        "all_dominant": [Segment(0, cut, "en"), Segment(cut, len(text), "en-US")],
    }[segments]
    detector, _ = make_detector(EMITS)

    result = detector.detect(text, "en", segments=given)

    assert result.segment_languages == ()
    assert result == detector.detect(text, "en")


def test_result_reports_nothing_when_the_segment_limit_makes_detection_single_language() -> None:
    from septum_core.detector import _MAX_SEGMENTS_PER_DOCUMENT

    text = "Mr Brown Smith attended. " + EN_FILLER * 80
    count = _MAX_SEGMENTS_PER_DOCUMENT + 1
    size = len(text) // count
    edges = [i * size for i in range(count)] + [len(text)]
    segments = [
        Segment(a, b, "tr" if i % 2 else "en") for i, (a, b) in enumerate(zip(edges, edges[1:]))
    ]
    detector, _ = make_detector(EMITS)

    assert detector.detect(text, "en", segments=segments).segment_languages == ()


def test_result_reports_nothing_when_segments_cannot_be_mapped_onto_the_text() -> None:
    text = "Mr Brown Smith attended. " + _distant_mark_text(60) + TR_FILLER * 3
    detector, _ = make_detector(EMITS)

    result = detector.detect(text, "en", segments=_distant_mark_segments(text))

    assert result.segment_languages == ()


def test_result_leaves_out_a_language_the_plan_runs_on_the_dominant_set_alone() -> None:
    text, cut = two_part_text()
    detector, registry = make_detector({})

    result = detector.detect(text, "en", segments=[Segment(0, cut, "en"), Segment(cut, len(text), "es")])

    assert result.segment_languages == ()
    assert registry.requested == [tuple(BASE)]


def test_result_leaves_out_a_dominant_set_language_next_to_one_with_models_of_its_own() -> None:
    en, tr = EN_FILLER * 20, TR_FILLER * 20
    es = "El comite reviso el presupuesto anual en detalle. " * 10
    text = en + tr + es
    detector, _ = make_detector(EMITS)

    result = detector.detect(
        text,
        "en",
        segments=[
            Segment(0, len(en), "en"),
            Segment(len(en), len(en + tr), "tr"),
            Segment(len(en + tr), len(text), "es"),
        ],
    )

    assert result.segment_languages == ("tr",)


def test_result_with_the_ner_layer_off_still_reports_the_segment_languages() -> None:
    text, cut = two_part_text()
    detector, _ = make_detector(
        EMITS, config=SeptumCoreConfig(use_presidio_layer=False, use_ner_layer=False)
    )

    result = detector.detect(text, "en", segments=[Segment(0, cut, "en"), Segment(cut, len(text), "tr")])

    assert result.segment_languages == ("tr",)


def test_result_reports_the_languages_applied_on_the_windowed_path() -> None:
    text, cut = two_part_text()
    detector, _ = make_detector(EMITS)

    result = detector.detect(
        text, "en", windowing=_SMALL, segments=[Segment(0, cut, "en"), Segment(cut, len(text), "tr")]
    )

    assert result.segment_languages == ("tr",)


def test_result_orders_languages_of_equal_share_by_first_appearance() -> None:
    detector, _ = make_detector({})
    text = TR_FILLER * 10
    half = len(text) // 2
    even = detector.detect(
        text, "en", segments=[Segment(0, half, "de"), Segment(half, len(text), "ru")]
    )
    assert even.segment_languages == ("de", "ru")
    flipped = detector.detect(
        text, "en", segments=[Segment(0, half, "ru"), Segment(half, len(text), "de")]
    )
    assert flipped.segment_languages == ("ru", "de")


def test_result_reports_the_segment_language_when_the_dominant_one_has_no_segment() -> None:
    text = TR_FILLER * 20
    detector, _ = make_detector(EMITS)

    result = detector.detect(text, "en", segments=[Segment(0, len(text), "tr")])

    assert result.segment_languages == ("tr",)


class _ContextPipeline(FakePipeline):
    """Tags its names only when its input also holds ``trigger``, as context sways a real model."""

    def __init__(self, trigger: str | None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._trigger = trigger

    def _emit_by_text(self, chunk: str) -> list[dict]:
        if self._trigger is not None and self._trigger not in chunk:
            return []
        return super()._emit_by_text(chunk)


class ContextRegistry(FakeRegistry):
    """:class:`FakeRegistry` whose models in ``triggers`` need their trigger word in the same input."""

    def __init__(self, emits, triggers: dict[str, str]) -> None:
        super().__init__(emits)
        self._triggers = triggers

    def get_pipelines_for_models(self, model_ids):
        ids = tuple(model_ids)
        self.requested.append(ids)
        fakes = [
            _ContextPipeline(self._triggers.get(mid), emit=self._emits.get(mid, {})) for mid in ids
        ]
        self.fakes.append(fakes)
        return [_WindowedNERPipeline(fake, None) for fake in fakes]


def _span_keys(result) -> set[tuple[int, int, str]]:
    return {(s.start, s.end, s.entity_type) for s in result.spans}


@pytest.mark.parametrize("shift", [-2, 0, 3, 5, 6, 11, 12])
@pytest.mark.parametrize("windowing", [WindowingPolicy(), _SMALL], ids=["whole", "windowed"])
def test_every_span_the_dominant_set_finds_without_segments_is_found_with_them(shift, windowing) -> None:
    """The dominant set needs a word far from the name; only a whole-text pass sees both."""
    head = "Ledger opened. " + EN_FILLER * 14
    tr = TR_FILLER * 7 + "Sayin Ayse Demir toplantiya katildi. " + TR_FILLER * 7
    tail = "Brown Smith attended. " + EN_FILLER * 6
    text = head + tr + tail
    cut = len(head + tr) + shift
    segments = _tiled(text, [len(head), cut], ["en", "tr", "en"])
    registry = ContextRegistry(
        {BASE[0]: {"Brown Smith": "PER", "Ledger": "ORG"}, TR_IDS[0]: {"Ayse Demir": "PER"}},
        {BASE[0]: "Ledger"},
    )
    detector = Detector(_NER_ONLY, ner_registry=registry)

    plain = detector.detect(text, "en", windowing=windowing)
    mixed = detector.detect(text, "en", windowing=windowing, segments=segments)

    assert _span_keys(plain) <= _span_keys(mixed)
    assert covered(mixed, text, "Ayse Demir") and mixed.segment_languages == ("tr",)
    if windowing == WindowingPolicy():
        assert covered(plain, text, "Brown Smith")


class DropsTextByLanguage:
    """Non-PII filter that drops spans reading ``needle`` when judged in one of ``languages``."""

    def __init__(self, needle: str, languages) -> None:
        self.needle = needle
        self.languages = set(languages)

    def filter_spans(self, text, language, spans):
        if language not in self.languages:
            return list(spans)
        return [s for s in spans if text[s.start : s.end] != self.needle]


@pytest.mark.parametrize(
    ("languages", "overrides"),
    [(["es", "en"], {}), (["en", "fr", "es"], {}), (["tr", "en"], {"tr": BASE[0]})],
)
@pytest.mark.parametrize("windowing", [WindowingPolicy(), _SMALL], ids=["whole", "windowed"])
def test_segments_adding_no_model_anywhere_are_byte_identical_to_none(
    languages, overrides, windowing
) -> None:
    """Post-processing stays single-language too: the name sits in the other-language segment."""
    text, _ = two_part_text()
    text += " Mail jane.doe@example.com now."
    step = len(text) // len(languages)
    segments = _tiled(text, [step * i for i in range(1, len(languages))], languages)
    name_at = text.index("Brown Smith")
    assert next(s for s in segments if s.start <= name_at < s.end).language != "en"
    detector, registry = make_detector(
        EMITS, overrides, config=SeptumCoreConfig(use_presidio_layer=True, use_ner_layer=True)
    )
    detector._non_pii_filter = DropsTextByLanguage("Brown Smith", {"en"})

    plain = detector.detect(text, "en", windowing=windowing)
    asked = len(registry.requested)
    mixed = detector.detect(text, "en", windowing=windowing, segments=segments)

    assert mixed == plain
    assert registry.requested[asked:] == registry.requested[:asked]
    assert plain.spans and not covered(plain, text, "Brown Smith")


def test_a_segment_whose_plan_is_the_dominant_set_gets_no_window_of_its_own() -> None:
    en = EN_FILLER * 10
    es = "La comision reviso el presupuesto anual en detalle. " * 10
    tr = TR_FILLER * 10
    text = en + es + tr
    detector, registry = make_detector(EMITS)

    detector.detect(text, "en", segments=_tiled(text, [len(en), len(en + es)], ["en", "es", "tr"]))

    assert registry.requested == [tuple(BASE), tuple(TR_IDS), tuple(TR_IDS)]
    (window,) = set(registry.chunks_of_call(1))
    assert text.endswith(window) and len(text) - len(window) > len(en)
