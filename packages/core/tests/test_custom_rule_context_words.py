from __future__ import annotations

"""Context-word gate of regex and keyword custom recognizers."""

from dataclasses import dataclass, field
from typing import List, Optional

import pytest
from septum_core.config import SeptumCoreConfig
from septum_core.detector import Detector
from septum_core.document_windowing import WindowingPolicy, plan_text_windows
from septum_core.ports import NullSemanticDetectionPort
from septum_core.recognizers.registry import CONTEXT_WINDOW_CHARS, RecognizerRegistry
from septum_core.regulations.composer import PolicyComposer


@dataclass
class _Rule:
    id: int = 1
    name: str = "rule"
    entity_type: str = "CODENAME"
    detection_method: str = "keyword_list"
    pattern: Optional[str] = None
    keywords: Optional[List[str]] = None
    llm_prompt: Optional[str] = None
    context_words: List[str] = field(default_factory=list)
    placeholder_label: str = "CODENAME"
    is_active: bool = True


def _keyword(context: List[str]):
    return RecognizerRegistry._build_keyword_recognizer(
        _Rule(keywords=["Alpha"], context_words=context)
    )


def _regex(context: List[str]):
    return RecognizerRegistry._build_regex_recognizer(
        _Rule(detection_method="regex", pattern=r"\bAB\d{4}\b", context_words=context)
    )


def _hits(recognizer, text: str) -> int:
    return len(recognizer.analyze(text=text, entities=["CODENAME"], nlp_artifacts=None))


@pytest.mark.parametrize("build,target", [(_keyword, "Alpha"), (_regex, "AB1234")])
class TestGate:
    def test_before_and_after_kept(self, build, target):
        rec = build(["patient"])
        assert _hits(rec, f"patient {target}") == 1
        assert _hits(rec, f"{target} of the patient") == 1

    def test_beyond_window_dropped(self, build, target):
        rec = build(["patient"])
        assert _hits(rec, f"patient{' ' * 101}{target}") == 0
        assert _hits(rec, f"{target}{' ' * 101}patient") == 0
        assert _hits(rec, f"patient{' ' * 90}{target}") == 1

    def test_absent_dropped(self, build, target):
        assert _hits(build(["patient"]), f"nothing here {target}") == 0

    def test_case_and_diacritics(self, build, target):
        assert _hits(build(["Patient"]), f"PATIENT {target}") == 1
        assert _hits(build(["hasta"]), f"HASTA {target}") == 1
        assert _hits(build(["müşteri"]), f"MUSTERI {target}") == 1
        assert _hits(build(["musteri"]), f"Müşteri {target}") == 1

    def test_token_boundary(self, build, target):
        rec = build(["ID"])
        assert _hits(rec, f"IDEA {target}") == 0
        assert _hits(rec, f"(id) {target}") == 1

    def test_empty_and_blank_unchanged(self, build, target):
        assert _hits(build([]), target) == 1
        assert _hits(build(["", "  "]), target) == 1


def test_match_counts_as_its_own_context() -> None:
    rec = _keyword(["alpha"])
    assert _hits(rec, "Alpha") == 1
    assert _hits(rec, "Alpha and alpha") == 2
    regex = RecognizerRegistry._build_regex_recognizer(
        _Rule(detection_method="regex", pattern=r"ID-\d+", context_words=["id"])
    )
    assert _hits(regex, "ref ID-123") == 1


def test_llm_prompt_rule_ignores_context_words() -> None:
    seen = []

    def factory(custom):
        seen.append(custom.context_words)
        return None

    registry = RecognizerRegistry(llm_recognizer_factory=factory)
    registry._from_custom_recognizers(
        [_Rule(detection_method="llm_prompt", llm_prompt="x", context_words=["w"])]
    )
    assert seen == [["w"]]


def _spans_of(recognizer, text: str) -> List[tuple]:
    results = recognizer.analyze(text=text, entities=["CODENAME"], nlp_artifacts=None)
    return sorted((r.start, r.end) for r in results)


@pytest.mark.parametrize("build,target", [(_keyword, "Alpha"), (_regex, "AB1234")])
def test_exact_window_edges(build, target) -> None:
    rec = build(["patient"])
    edge = CONTEXT_WINDOW_CHARS
    assert _hits(rec, f"patient{' ' * (edge - 1)}{target}") == 1
    assert _hits(rec, f"patient{' ' * edge}{target}") == 0
    assert _hits(rec, f"{target}{' ' * (edge - 1)}patient") == 1
    assert _hits(rec, f"{target}{' ' * edge}patient") == 0


def test_word_cut_by_window_edge_does_not_count() -> None:
    rec = _keyword(["ID"])
    edge = CONTEXT_WINDOW_CHARS
    before = f"XID{' ' * (edge - 2)}Alpha"
    assert before.index("Alpha") - before.index("ID") == edge
    assert _hits(rec, before) == 0
    after = f"Alpha{' ' * (edge - 2)}IDX"
    assert _hits(rec, after) == 0


def test_word_crossing_window_edge_counts() -> None:
    rec = _keyword(["patient"])
    edge = CONTEXT_WINDOW_CHARS
    assert _hits(rec, f"patient{' ' * (edge - 3)}Alpha") == 1
    assert _hits(rec, f"Alpha{' ' * (edge - 3)}patient") == 1


def test_only_matches_near_context_are_kept() -> None:
    rec = _keyword(["patient"])
    text = f"patient Alpha{' ' * 300}Alpha"
    assert _spans_of(rec, text) == [(8, 13)]


def test_dotless_and_dotted_forms_fold_together() -> None:
    assert _hits(_keyword(["kimlik"]), "KIMLIK Alpha") == 1
    assert _hits(_keyword(["kimlik"]), "kımlık Alpha") == 1
    assert _hits(_keyword(["KİMLİK"]), "kimlik Alpha") == 1
    assert _hits(_keyword(["kımlık"]), "Kimlik Alpha") == 1


def test_whitespace_runs_in_phrase() -> None:
    rec = _keyword(["patient  id"])
    assert _hits(rec, "patient\n\tid Alpha") == 1
    assert _hits(rec, "patient id Alpha") == 1
    assert _hits(rec, "patientid Alpha") == 0


def test_unspaced_script_context_word() -> None:
    rec = _keyword(["患者"])
    assert _hits(rec, "患者 Alpha") == 1
    assert _hits(rec, "联系患者获取 Alpha") == 1
    assert _hits(rec, "患者Alpha") == 0


def test_decomposed_accents_keep_source_offsets() -> None:
    rec = _keyword(["musteri"])
    decomposed = "mu\u0308s\u0327teri"
    assert _hits(rec, f"{decomposed} Alpha") == 1
    edge = CONTEXT_WINDOW_CHARS
    near = f"{decomposed}{' ' * (edge - 1)}Alpha"
    far = f"{decomposed}{' ' * edge}Alpha"
    assert (_hits(rec, near), _hits(rec, far)) == (1, 0)
    assert _hits(_keyword(["ID"]), f"X\u0301ID{' ' * (edge - 2)}Alpha") == 0


def test_each_match_judged_on_its_own_window() -> None:
    rec = _keyword(["patient"])
    gap = " " * (CONTEXT_WINDOW_CHARS + 50)
    text = gap.join(["patient Alpha", "Alpha", "Alpha patient", "Alpha"])
    starts = [i for i in range(len(text)) if text.startswith("Alpha", i)]
    assert _spans_of(rec, text) == [(starts[0], starts[0] + 5), (starts[2], starts[2] + 5)]


def _detector(context: List[str], context_gate: bool = True) -> Detector:
    policy = PolicyComposer().compose_from_data(
        [], [_Rule(keywords=["zorblax"], context_words=context)], []
    )
    return Detector(
        config=SeptumCoreConfig(use_presidio_layer=True, use_ner_layer=False),
        policy=policy,
        semantic_port=NullSemanticDetectionPort(),
        context_gate=context_gate,
    )


def _codenames(detector: Detector, text: str, **kwargs) -> List[tuple]:
    result = detector.detect(text, "en", **kwargs)
    return sorted((s.start, s.end) for s in result.spans if s.entity_type == "CODENAME")


def test_detector_gates_custom_rule() -> None:
    detector = _detector(["project"])
    assert _codenames(detector, "the project zorblax ships") == [(12, 19)]
    assert _codenames(detector, "the plan zorblax ships") == []


def test_pattern_spans_gate_custom_rule() -> None:
    detector = _detector(["project"])
    kept = [(s.start, s.end) for s in detector._pattern_spans("project zorblax")]
    assert kept == [(8, 15)]
    assert detector._pattern_spans("plan zorblax") == []


def test_detector_without_gate_keeps_every_match() -> None:
    detector = _detector(["project"], context_gate=False)
    assert (detector.context_gate, _detector(["project"]).context_gate) == (False, True)
    assert _codenames(detector, "the plan zorblax ships") == [(9, 16)]
    assert _codenames(_detector(["project"]), "the plan zorblax ships") == []


def test_windowed_detection_sees_context_across_window_cut() -> None:
    policy = WindowingPolicy(max_whole_chars=300, window_chars=1000, overlap_chars=10)
    filler = " ".join(["lorem"] * 400)
    text = filler[:950] + " project " + filler[:50] + " zorblax " + filler
    windows = plan_text_windows(text, policy)
    word = text.index("project")
    match = text.index("zorblax")
    assert match - (word + len("project")) <= CONTEXT_WINDOW_CHARS
    assert not any(
        w.start <= word and match + len("zorblax") <= w.end for w in windows
    )
    detector = _detector(["project"])
    assert _codenames(detector, text, windowing=policy) == [(match, match + 7)]
    far = filler[:950] + " plan " + filler[:50] + " zorblax " + filler
    assert _codenames(detector, far, windowing=policy) == []


@pytest.mark.parametrize(
    "word,text",
    [
        ("No:", "No: 12345"),
        ("Tel.", "Tel. 12345"),
        ("T.C.", "T.C. 12345"),
        ("#id", "#id 12345"),
        ("(ref)", "(ref) 12345"),
    ],
)
def test_punctuation_edge_context_word(word: str, text: str) -> None:
    rec = RecognizerRegistry._build_regex_recognizer(
        _Rule(detection_method="regex", pattern=r"\b\d{5}\b", context_words=[word])
    )
    assert _hits(rec, text) == 1
    assert _hits(rec, "nothing here 12345") == 0


def test_punctuation_edge_keeps_word_edge_boundary() -> None:
    rec = RecognizerRegistry._build_regex_recognizer(
        _Rule(detection_method="regex", pattern=r"\b\d{5}\b", context_words=["No:"])
    )
    assert _hits(rec, "XNo: 12345") == 0
    assert _hits(rec, "(No: 12345") == 1


@pytest.mark.parametrize("fold", ["ß", "ﬁ"])
def test_multi_character_fold_before_context_keeps_window_edge(fold: str) -> None:
    rec = _keyword(["patient"])
    edge = CONTEXT_WINDOW_CHARS
    prefix = fold * 10 + " "
    near = f"{prefix}patient{' ' * (edge - 1)}Alpha"
    far = f"{prefix}patient{' ' * edge}Alpha"
    assert near.index("Alpha") - (near.index("patient") + len("patient") - 1) == edge
    assert (_hits(rec, near), _hits(rec, far)) == (1, 0)


def test_context_word_that_folds_to_several_characters() -> None:
    assert _hits(_keyword(["straße"]), "STRASSE Alpha") == 1
    assert _hits(_keyword(["strasse"]), "Straße Alpha") == 1


def test_long_phrase_reach_survives_shorter_later_hits() -> None:
    phrase = "a b " + " ".join(["filler"] * 21)
    assert 140 <= len(phrase) <= 160
    rec = _keyword(["b", phrase])
    pad = 190 - len(phrase)
    text = f"{phrase}{' ' * pad}Alpha"
    assert text.index("Alpha") - (len(phrase) - 1) <= CONTEXT_WINDOW_CHARS
    assert text.index("Alpha") - text.index(" b ") > CONTEXT_WINDOW_CHARS
    assert _hits(rec, text) == 1
    assert _hits(_keyword(["b"]), text) == 0


def test_longer_overlapping_context_word_reaches_window() -> None:
    edge = CONTEXT_WINDOW_CHARS
    text = f"patient id{' ' * (edge - 2)}Alpha"
    assert text.index("Alpha") - text.index("id") == edge
    assert _hits(_keyword(["patient", "patient id"]), text) == 1
    assert _hits(_keyword(["patient"]), text) == 0


@pytest.mark.parametrize("space", [" ", " "])
def test_unicode_space_inside_phrase(space: str) -> None:
    assert _hits(_keyword([f"patient{space}id"]), "patient id Alpha") == 1
    assert _hits(_keyword(["patient id"]), f"patient{space}id Alpha") == 1


def test_trailing_combining_mark_at_window_edge_counts() -> None:
    rec = _keyword(["cafe"])
    edge = CONTEXT_WINDOW_CHARS
    word = "café"
    near = f"{word}{' ' * (edge - 1)}Alpha"
    far = f"{word}{' ' * edge}Alpha"
    assert near.index("Alpha") - near.index("́") == edge
    assert (_hits(rec, near), _hits(rec, far)) == (1, 0)
