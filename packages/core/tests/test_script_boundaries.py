"""Script-aware boundaries reach the detector, the person-name expansion and the e-mail recognizer."""

from __future__ import annotations

import importlib

import pytest
from septum_core import SeptumCoreConfig, SeptumEngine
from septum_core.detector import Detector
from septum_core.language_resolution import _SCRIPT_RULES
from septum_core.ports import NullSemanticDetectionPort
from septum_core.recognizers import BUILTIN_REGULATION_IDS
from septum_core.recognizers.email import EmailAddressRecognizer
from septum_core.span_processing import expand_person_name_spans
from septum_core.spans import DetectedSpan


def _ner(text: str, items: list[tuple[str, int, int, float]], language: str) -> list[tuple[int, int, str]]:
    raw = [
        {"entity_group": label, "start": start, "end": end, "score": score}
        for label, start, end, score in items
    ]
    return [
        (span.start, span.end, span.entity_type)
        for span in Detector._from_ner_results(raw, text, language)
    ]


def test_zh_person_and_location_cover_only_their_glyphs() -> None:
    text = "张伟在北京工作"
    spans = _ner(text, [("PER", 0, 2, 0.99), ("LOC", 3, 5, 0.99)], "zh")
    assert spans == [(0, 2, "PERSON_NAME"), (3, 5, "LOCATION")]


def test_zh_person_expansion_does_not_absorb_the_clause() -> None:
    text = "张伟在北京工作"
    expanded = expand_person_name_spans(
        text, [DetectedSpan(start=0, end=2, entity_type="PERSON_NAME", score=0.9)]
    )
    assert [(s.start, s.end) for s in expanded] == [(0, 2)]


def test_two_glyph_han_location_is_not_gated_as_single_word() -> None:
    text = "张伟在北京工作"
    assert _ner(text, [("LOC", 3, 5, 0.90)], "zh") == [(3, 5, "LOCATION")]


def test_single_latin_word_location_still_needs_the_single_word_floor() -> None:
    text = "Ankara'da yaşıyor"
    assert _ner(text, [("LOC", 0, 6, 0.90)], "tr") == []
    assert _ner(text, [("LOC", 0, 6, 0.97)], "tr") == [(0, 6, "LOCATION")]


def test_thai_person_hit_masks_the_whole_run() -> None:
    run = "นายสมชายใจดีทำงานที่กรุงเทพ"
    text = run + " วันนี้"
    assert _ner(text, [("PER", 3, 8, 0.99)], "th") == [(0, len(run), "PERSON_NAME")]


def test_thai_whole_run_location_still_needs_the_single_word_floor() -> None:
    text = "ทำงานที่กรุงเทพ วันนี้"
    assert _ner(text, [("LOC", 8, 15, 0.90)], "th") == []
    assert _ner(text, [("LOC", 8, 15, 0.97)], "th") == [(0, 15, "LOCATION")]


def test_only_the_thai_script_snaps_to_the_whole_run() -> None:
    assert {script for script, rule in _SCRIPT_RULES.items() if rule.snap_whole_run} == {"Thai"}


def test_thai_name_in_an_english_document_masks_the_whole_run() -> None:
    run = "คุณวิชัยไปพบแพทย์ที่ภูเก็ต"
    text = f"Contact {run} for details."
    name = text.index("วิชัย")
    assert _ner(text, [("PER", name, name + 5, 0.99)], "en") == [
        (8, 8 + len(run), "PERSON_NAME")
    ]


def test_han_name_in_a_thai_document_snaps_precisely() -> None:
    text = "张伟在北京工作"
    assert _ner(text, [("PER", 0, 2, 0.99)], "th") == [(0, 2, "PERSON_NAME")]


def test_touching_person_glyphs_are_joined_before_the_length_floor() -> None:
    text = "王芳在北京"
    assert _ner(text, [("PER", 0, 1, 0.90), ("PER", 1, 2, 0.90)], "zh") == [
        (0, 2, "PERSON_NAME")
    ]


def test_joined_groups_keep_the_higher_score() -> None:
    raw = [
        {"entity_group": "PER", "start": 0, "end": 1, "score": 0.97},
        {"entity_group": "PER", "start": 1, "end": 2, "score": 0.85},
    ]
    assert [s.score for s in Detector._from_ner_results(raw, "王芳在北京", "zh")] == [0.97]
    assert [s.score for s in Detector._from_ner_results(raw[::-1], "王芳在北京", "zh")] == [0.97]
    reverse = [
        {"entity_group": "PER", "start": 0, "end": 1, "score": 0.85},
        {"entity_group": "PER", "start": 1, "end": 2, "score": 0.97},
    ]
    assert [s.score for s in Detector._from_ner_results(reverse, "王芳在北京", "zh")] == [0.97]


def test_overlapping_or_gapped_groups_are_not_joined() -> None:
    def merged(items: list[tuple[int, int]]) -> list[tuple[int, int]]:
        raw = [{"entity_group": "PER", "start": s, "end": e, "score": 0.99} for s, e in items]
        return [(start, end) for _, start, end, _ in Detector._merge_touching_ner_groups(raw)]

    assert merged([(0, 2), (1, 3)]) == [(0, 2), (1, 3)]
    assert merged([(0, 1), (2, 3)]) == [(0, 1), (2, 3)]
    assert merged([(0, 1), (1, 3)]) == [(0, 3)]


def test_empty_groups_yield_no_span() -> None:
    assert _ner("北京a", [("LOC", 3, 3, 0.99)], "zh") == []
    assert _ner("北京", [("PER", 0, 0, 0.99), ("PER", 1, 0, 0.99)], "zh") == []
    assert _ner("王芳", [("PER", 1, 1, 0.99), ("PER", 0, 2, 0.99)], "zh") == [
        (0, 2, "PERSON_NAME")
    ]


def test_touching_groups_of_different_types_stay_apart() -> None:
    text = "王芳北京市"
    assert _ner(text, [("PER", 0, 2, 0.99), ("LOC", 2, 5, 0.99)], "zh") == [
        (0, 2, "PERSON_NAME"),
        (2, 5, "LOCATION"),
    ]


def test_whitespace_separated_person_groups_are_not_joined_here() -> None:
    text = "Ali Veli geldi"
    assert _ner(text, [("PER", 0, 3, 0.99), ("PER", 4, 8, 0.99)], "tr") == [
        (0, 3, "PERSON_NAME"),
        (4, 8, "PERSON_NAME"),
    ]


def test_single_glyph_person_needs_high_confidence() -> None:
    text = "老王来了"
    assert _ner(text, [("PER", 1, 2, 0.97)], "zh") == [(1, 2, "PERSON_NAME")]
    assert _ner(text, [("PER", 1, 2, 0.90)], "zh") == []
    assert _ner(text, [("PER", 1, 2, 0.95)], "zh") == [(1, 2, "PERSON_NAME")]
    assert _ner(text, [("PER", 1, 2, 0.949)], "zh") == []
    assert _ner(text, [("LOC", 1, 2, 0.99)], "zh") == []
    assert _ner("A came", [("PER", 0, 1, 0.99)], "zh") == []


def test_mixed_script_spans_stop_at_the_script_edge() -> None:
    assert _ner("Ali在北京", [("PER", 0, 3, 0.99), ("LOC", 4, 6, 0.99)], "zh") == [
        (0, 3, "PERSON_NAME"),
        (4, 6, "LOCATION"),
    ]
    assert _ner("Ali在北京", [("LOC", 3, 5, 0.99)], "zh") == [(3, 5, "LOCATION")]
    assert _ner("Zhang在北京", [("PER", 0, 3, 0.99)], "zh") == [(0, 5, "PERSON_NAME")]
    assert _ner("张伟123在", [("PER", 0, 2, 0.99)], "zh") == [(0, 2, "PERSON_NAME")]


def test_spaced_hangul_location_gate_counts_whitespace_words() -> None:
    text = "한국 서울"
    assert _ner(text, [("LOC", 0, 2, 0.90)], "ko") == []
    assert _ner(text, [("LOC", 0, 5, 0.90)], "ko") == [(0, 5, "LOCATION")]


def test_person_expansion_takes_a_latin_surname_but_stops_at_han() -> None:
    text = "请联系Zhang Wei在北京工作"
    expanded = expand_person_name_spans(
        text, [DetectedSpan(start=3, end=8, entity_type="PERSON_NAME", score=0.9)]
    )
    assert [(s.start, s.end) for s in expanded] == [(3, 12)]


def test_person_expansion_takes_a_latin_given_name_but_stops_at_han() -> None:
    text = "在Zhang Wei"
    expanded = expand_person_name_spans(
        text, [DetectedSpan(start=7, end=10, entity_type="PERSON_NAME", score=0.9)]
    )
    assert [(s.start, s.end) for s in expanded] == [(1, 10)]


def test_no_regulation_pack_ships_its_own_email_recognizer() -> None:
    assert BUILTIN_REGULATION_IDS
    for regulation_id in BUILTIN_REGULATION_IDS:
        module = importlib.import_module(f"septum_core.recognizers.{regulation_id}.recognizers")
        for recognizer in module.get_recognizers():
            assert "EMAIL_ADDRESS" not in recognizer.supported_entities, regulation_id


def test_one_email_recognizer_is_registered() -> None:
    engine = SeptumEngine(
        regulations=["gdpr"],
        config=SeptumCoreConfig(use_presidio_layer=True, use_ner_layer=False),
        semantic_port=NullSemanticDetectionPort(),
    )
    recognizers = engine._detector._analyzer.registry.get_recognizers(language="en", all_fields=True)
    email = [r for r in recognizers if "EMAIL_ADDRESS" in r.supported_entities]
    assert len(email) == 1


@pytest.mark.parametrize("left, right", [("联系", "获取"), ("连络", "まで"), ("ติดต่อ", "ครับ")])
def test_email_next_to_unspaced_glyphs_is_matched_whole(left: str, right: str) -> None:
    # Glyphs glued to the local part are masked with it (they may be a name);
    # the domain still ends before the glyphs after it.
    email = "zhang.wei@example.com"
    text = left + email + right
    results = EmailAddressRecognizer().analyze(text, ["EMAIL_ADDRESS"], None)
    assert [(r.start, r.end) for r in results] == [(0, len(left) + len(email))]


def test_zh_email_next_to_han_is_masked_whole() -> None:
    engine = SeptumEngine(
        regulations=["gdpr"],
        config=SeptumCoreConfig(use_presidio_layer=True, use_ner_layer=False),
        semantic_port=NullSemanticDetectionPort(),
    )
    result = engine.mask("请联系zhang.wei@example.com获取资料", language="zh")
    assert "zhang.wei" not in result.masked_text
    assert "example.com" not in result.masked_text
    assert result.entity_count == 1
