"""Pin the exact output of the span, NER-group and sanitize pipeline stages.

A seeded corpus drives each stage; every case is reduced to a digest and
compared with ``complexity_golden.json``. A refactor of these stages must
leave every digest unchanged. Regenerate the golden file only for a
deliberate behaviour change:
``python tests/test_complexity_characterization.py <output.json>``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from septum_core.anonymization_map import AnonymizationMap
from septum_core.config import SeptumCoreConfig
from septum_core.detector import (
    _HIGH_PRIORITY_ENTITY_TYPES,
    Detector,
    StructuralAddressRecognizer,
)
from septum_core.ports import NullSemanticDetectionPort
from septum_core.span_processing import (
    absorb_overlapping_spans,
    deduplicate_spans,
    expand_person_name_spans,
    merge_adjacent_person_name_spans,
)
from septum_core.spans import DetectedSpan

_GOLDEN_PATH = Path(__file__).parent / "complexity_golden.json"

_FRAGMENTS = (
    "Kaya", "Ahmet", "Yılmaz", "O’Brien", "ali", "x1", "12", "3rd", "A_b",
    "İstanbul", "Ünal", "你好", "世界", "สวัสดี", "é", "Zoë", "ÇAĞRI",
)
_SEPARATORS = (" ", " ", " ", "  ", "\n", "\r\n", "'", "’", "-", ".", ", ", "", "\t")
_TYPES = (
    "PERSON_NAME", "PERSON_NAME", "PERSON_NAME", "LOCATION", "ORGANIZATION_NAME",
    "DATE", "DATE_OF_BIRTH", "POSTAL_ADDRESS", "STREET_ADDRESS", "PHONE_NUMBER",
    "NATIONAL_ID", "EMAIL_ADDRESS", "URL", "IBAN",
)
_SCORES = (0.3, 0.6, 0.75, 0.85, 0.95)


def _digest(value: object) -> str:
    return hashlib.sha1(repr(value).encode("utf-8")).hexdigest()[:12]


def _span_repr(span: object) -> tuple:
    return (
        span.start,
        span.end,
        span.entity_type,
        span.score,
        getattr(span, "core", None),
    )


def _spans_repr(spans: list) -> list:
    return [_span_repr(s) for s in spans]


def _fuzz_text(rng: random.Random) -> tuple[str, list[tuple[int, int]]]:
    parts: list[str] = []
    bounds: list[tuple[int, int]] = []
    pos = 0
    for _ in range(rng.randint(2, 8)):
        word = rng.choice(_FRAGMENTS)
        bounds.append((pos, pos + len(word)))
        parts.append(word)
        pos += len(word)
        sep = rng.choice(_SEPARATORS)
        parts.append(sep)
        pos += len(sep)
    return "".join(parts), bounds


def _fuzz_spans(
    rng: random.Random, text: str, bounds: list[tuple[int, int]]
) -> list[DetectedSpan]:
    spans = []
    for _ in range(rng.randint(1, 5)):
        if rng.random() < 0.75:
            first = rng.randrange(len(bounds))
            last = min(len(bounds) - 1, first + rng.choice((0, 0, 1)))
            start, end = bounds[first][0], bounds[last][1]
            if rng.random() < 0.3:
                end = max(start, end - rng.randint(0, 3))
            if rng.random() < 0.2:
                start = min(end, start + rng.randint(0, 2))
        else:
            start = rng.randint(-2, len(text) + 2)
            end = start + rng.randint(-1, 8)
        core = None
        if rng.random() < 0.2 and end - start > 2:
            core = (start + rng.randint(0, 1), end - rng.randint(0, 2))
        spans.append(
            DetectedSpan(start, end, rng.choice(_TYPES), rng.choice(_SCORES), core)
        )
    return spans


def _span_cases(count: int = 1200) -> list[tuple[str, list[DetectedSpan]]]:
    rng = random.Random(20260101)
    cases = []
    for _ in range(count):
        text, bounds = _fuzz_text(rng)
        cases.append((text, _fuzz_spans(rng, text, bounds)))
    return cases


def _copy(spans: list[DetectedSpan]) -> list[DetectedSpan]:
    return [DetectedSpan(s.start, s.end, s.entity_type, s.score, s.core) for s in spans]


def _span_processing_digests() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {
        k: [] for k in ("absorb", "absorb_raw", "dedupe", "dedupe_raw", "expand", "merge", "chain")
    }
    for text, spans in _span_cases():
        out["absorb"].append(_digest(_spans_repr(absorb_overlapping_spans(_copy(spans), text))))
        out["absorb_raw"].append(_digest(_spans_repr(absorb_overlapping_spans(_copy(spans)))))
        out["dedupe"].append(_digest(_spans_repr(
            deduplicate_spans(_copy(spans), _HIGH_PRIORITY_ENTITY_TYPES, text)
        )))
        out["dedupe_raw"].append(_digest(_spans_repr(
            deduplicate_spans(_copy(spans), {"PHONE_NUMBER"})
        )))
        clean = deduplicate_spans(_copy(spans), _HIGH_PRIORITY_ENTITY_TYPES, text)
        out["expand"].append(_digest(_spans_repr(expand_person_name_spans(text, _copy(spans)))))
        out["merge"].append(_digest(_spans_repr(merge_adjacent_person_name_spans(text, _copy(spans)))))
        chain = expand_person_name_spans(text, clean)
        chain = merge_adjacent_person_name_spans(text, chain)
        out["chain"].append(_digest(_spans_repr(chain)))
    return out


def _ner_groups_cases() -> list[list[dict]]:
    rng = random.Random(77)
    labels = ("PER", "ORG", "LOC", "GPE", "EMAIL", "MISC", "I-PER", "B-LOC", "", None)
    cases = []
    for _ in range(600):
        items = []
        for _ in range(rng.randint(0, 6)):
            start = rng.randint(0, 30)
            item: dict = {
                "start": start,
                "end": start + rng.randint(-1, 9),
                "score": rng.choice((0.2, 0.5, 0.8, 0.9, 0.96, 0.99)),
            }
            label = rng.choice(labels)
            kind = rng.choice(("entity_group", "entity", "entity_group", "none"))
            if kind != "none":
                item[kind] = label
            if rng.random() < 0.1:
                del item["score"]
            items.append(item)
        if rng.random() < 0.3 and items:
            touching = dict(items[0])
            touching["start"] = items[0]["end"]
            touching["end"] = touching["start"] + rng.randint(1, 4)
            items.append(touching)
        cases.append(items)
    return cases


_NER_TEXTS = (
    "Ahmet Yılmaz lives in Berlin and works at Acme Corp today.",
    "Ali met Veli at İstanbul Technical University last week.",
    "王芳和李明在北京大学工作。张伟也在。",
    "สมชาย ใจดี อาศัยอยู่ที่กรุงเทพ",
    "김민준 씨는 서울에 삽니다 田中太郎さん",
    "Zoë Çelik <zoe@example.com> met Kaya",
    "a b c d e f g h i j k l m n o p q r s t u v w x y z 0 1 2 3",
)


def _ner_results_cases() -> list[tuple[list[dict], str, str]]:
    rng = random.Random(4242)
    labels = ("PER", "ORG", "LOC", "EMAIL", "MISC")
    languages = ("en", "tr", "zh", "ja", "ko", "th", "xx", "de-DE")
    cases = []
    for _ in range(700):
        text = rng.choice(_NER_TEXTS)
        items = []
        for _ in range(rng.randint(0, 5)):
            start = rng.randint(0, max(0, len(text) - 1))
            items.append({
                "entity_group": rng.choice(labels),
                "start": start,
                "end": min(len(text), start + rng.randint(0, 14)),
                "score": rng.choice((0.5, 0.85, 0.9, 0.94, 0.96, 0.99)),
            })
        if items and rng.random() < 0.4:
            nxt = dict(items[0])
            nxt["start"] = items[0]["end"]
            nxt["end"] = min(len(text), nxt["start"] + rng.randint(1, 3))
            items.append(nxt)
        cases.append((items, text, rng.choice(languages)))
    return cases


def _ner_digests() -> dict[str, list]:
    merged = [
        _digest([tuple(g) for g in Detector._merge_touching_ner_groups(items)])
        for items in _ner_groups_cases()
    ]
    converted = [
        _digest(_spans_repr(Detector._from_ner_results(items, text, language)))
        for items, text, language in _ner_results_cases()
    ]
    return {"ner_merge": merged, "ner_convert": converted}


def _presidio_filter_digests() -> dict[str, list]:
    rng = random.Random(9)
    texts = ("555.123.4567", "+90 555 123 45 67", "12/34", "John Smith in Paris", "1.2.3.4 5678")
    types = ("PERSON_NAME", "PHONE_NUMBER", "LOCATION", "EMAIL_ADDRESS")
    out = []
    for use_ner in (True, False):
        detector = Detector.__new__(Detector)
        detector._config = SeptumCoreConfig(use_ner_layer=use_ner)
        for _ in range(150):
            text = rng.choice(texts)
            results = [
                SimpleNamespace(
                    entity_type=rng.choice(types),
                    start=0,
                    end=rng.randint(1, len(text)),
                    score=rng.choice((0.4, 0.69, 0.7, 0.71, 0.9)),
                )
                for _ in range(rng.randint(0, 4))
            ]
            kept = detector._filter_presidio_results(results, text)
            out.append(_digest([(r.entity_type, r.start, r.end, r.score) for r in kept]))
        out.append(_digest(detector._filter_presidio_results([], "x")))
    return {"presidio_filter": out}


_ADDRESS_TEXTS = (
    "",
    "Adres: Atatürk Mah. 12 Sok. No: 5 Daire 3 Kadıköy",
    "Address: 12 Main St. Apt 4B Springfield 90210",
    "Address: short",
    "Note: nothing here to see at all really",
    "Name: John\nAddress: 5 Elm Ave. Blk 7 Unit 9\nPhone: 555",
    "X. 428 Y. Z NO: 10",
    "MAH 5 SK 6 NO 7\nsecond line with no cues\nCD. 3 KAT 2 DAIRE 1",
    "St 1 Ave 2\nSt Ave\nno digits St Ave",
    "Konum: Bldg 2 Flat 3 / 5-7\nAdres : Flat 9",
    "Label: A: 1 B: 2 C. 3",
    "Ulaşım: Blvd 5 Blk 3 Kat 2 No 1 ve devamı",
    "\n\n  \nSK 1 CD 2\n",
    "Tel: 1-2-3 / 4 NO:5",
    "Adr: NO 1 NO 2",
    "Label:\nNO 1 NO 2 stuff here",
)


def _address_digests() -> dict[str, list]:
    out = []
    for abbreviations in (None, frozenset({"RD", "LN"})):
        recognizer = StructuralAddressRecognizer(abbreviations)
        for text in _ADDRESS_TEXTS + ("RD 1 LN 2", "Label: RD 5 LN 7 Box 12"):
            for entities in (None, ["POSTAL_ADDRESS"], ["EMAIL_ADDRESS"], ["LOCATION"]):
                results = recognizer.analyze(text, entities)
                out.append(
                    _digest([(r.entity_type, r.start, r.end, r.score) for r in results])
                )
    return {"address": out}


class _Pipeline:
    """Fake NER pipeline: tags every occurrence of the needles it is given."""

    def __init__(self, needles: dict[str, str], score: float = 0.97) -> None:
        self._needles = needles
        self._score = score
        self.seen: list[str] = []

    def __call__(self, text: str) -> list[dict]:
        self.seen.append(text)
        results = []
        for needle, label in self._needles.items():
            at = text.lower().find(needle.lower())
            if at >= 0:
                results.append({
                    "entity_group": label,
                    "start": at,
                    "end": at + len(needle),
                    "score": self._score,
                })
        return results


class _Registry:
    def __init__(self, pipelines: list) -> None:
        self._pipelines = pipelines

    def get_pipelines(self, language: str) -> list:
        return self._pipelines


class _Analyzer:
    def __init__(self, results: list) -> None:
        self._results = results
        self.calls: list[tuple] = []

    def analyze(self, text, language, entities):
        self.calls.append((language, None if entities is None else list(entities)))
        return list(self._results)


class _Port:
    def __init__(self, **behaviour) -> None:
        self.b = behaviour
        self.calls: list[str] = []

    def is_enabled(self) -> bool:
        return self.b.get("enabled", True)

    def validate_candidates(self, *, text, candidate_spans, language, regulation_context):
        self.calls.append(f"validate:{len(candidate_spans)}:{regulation_context}")
        if self.b.get("validate_raises"):
            raise RuntimeError("boom")
        mode = self.b.get("validate", "all")
        if mode == "none":
            return []
        if mode == "first":
            return candidate_spans[:1]
        return list(candidate_spans)

    def detect_aliases(self, *, normalized_text):
        self.calls.append("aliases")
        if self.b.get("aliases_raises"):
            raise RuntimeError("boom")
        return list(self.b.get("aliases", []))

    def detect_semantic(self, *, normalized_text, entity_types):
        self.calls.append(f"semantic:{sorted(entity_types)}")
        if self.b.get("semantic_raises"):
            raise RuntimeError("boom")
        return list(self.b.get("semantic", []))

    def resolve_coreference(self, *, normalized_text, anon_map, language):
        self.calls.append("coref")
        if self.b.get("coref_raises"):
            raise RuntimeError("boom")
        return 0


class _Filter:
    def __init__(self, drop_types: tuple[str, ...]) -> None:
        self._drop = drop_types

    def filter_spans(self, *, text, language, spans):
        return [s for s in spans if s.entity_type not in self._drop]


def _rr(entity_type: str, start: int, end: int, score: float) -> SimpleNamespace:
    return SimpleNamespace(entity_type=entity_type, start=start, end=end, score=score)


_LONG = (
    "Ahmet Yılmaz works with Maria Garcia in Berlin and the team reviews "
    "contracts every single week without fail"
)
_ALL_CAPS = "AHMET YILMAZ met Maria Garcia at ACME HOLDINGS in Berlin"

_SANITIZE_SCENARIOS: list[dict] = [
    {"id": "empty", "text": ""},
    {"id": "plain_presidio", "text": _LONG, "presidio": [_rr("PHONE_NUMBER", 0, 5, 0.8)]},
    {"id": "presidio_off_ner_off", "text": _LONG, "cfg": {"use_presidio_layer": False, "use_ner_layer": False}},
    {"id": "ner_only", "text": _LONG, "cfg": {"use_presidio_layer": False},
     "ner": [{"Ahmet Yılmaz": "PER", "Berlin": "LOC"}]},
    {"id": "ner_two_models", "text": _LONG, "cfg": {"use_presidio_layer": False},
     "ner": [{"Ahmet Yılmaz": "PER"}, {"Maria Garcia": "PER", "Ahmet": "PER"}]},
    {"id": "ner_allcaps_recovery", "text": _ALL_CAPS, "cfg": {"use_presidio_layer": False},
     "ner": [{"Ahmet Yilmaz": "PER", "Maria Garcia": "PER", "Acme Holdings": "ORG"}]},
    {"id": "ner_entity_filter", "text": _LONG, "cfg": {"use_presidio_layer": False},
     "ner": [{"Ahmet Yılmaz": "PER", "Berlin": "LOC"}], "entity_types": ["PERSON_NAME"]},
    {"id": "ner_whitespace_only", "text": "   \n ", "cfg": {"use_presidio_layer": False}, "ner": [{}]},
    {"id": "presidio_person_filtered", "text": _LONG,
     "presidio": [_rr("PERSON_NAME", 0, 12, 0.5), _rr("PERSON_NAME", 18, 30, 0.9),
                  _rr("LOCATION", 41, 47, 0.9)], "cfg": {"use_ner_layer": False}},
    {"id": "presidio_aliases", "text": _LONG, "entity_types": ["CREDIT_CARD_NUMBER", "BANK_ACCOUNT_NUMBER"],
     "presidio": [_rr("CREDIT_CARD", 0, 5, 0.9), _rr("IBAN_CODE", 6, 12, 0.9)], "cfg": {"use_ner_layer": False}},
    {"id": "validate_all", "text": _LONG, "cfg": {"use_semantic_validation": True},
     "presidio": [_rr("PHONE_NUMBER", 0, 5, 0.8), _rr("LOCATION", 41, 47, 0.9)],
     "port": {"validate": "all"}},
    {"id": "validate_first", "text": _LONG, "cfg": {"use_semantic_validation": True, "use_ner_layer": False},
     "presidio": [_rr("PHONE_NUMBER", 0, 5, 0.8), _rr("LOCATION", 41, 47, 0.9), _rr("DATE", 60, 66, 0.9)],
     "port": {"validate": "first"}, "entity_types": ["PHONE_NUMBER", "LOCATION"]},
    {"id": "validate_none", "text": _LONG, "cfg": {"use_semantic_validation": True, "use_ner_layer": False},
     "presidio": [_rr("LOCATION", 41, 47, 0.9)], "port": {"validate": "none"}},
    {"id": "validate_raises", "text": _LONG, "cfg": {"use_semantic_validation": True, "use_ner_layer": False},
     "presidio": [_rr("LOCATION", 41, 47, 0.9)], "port": {"validate_raises": True}},
    {"id": "validate_port_disabled", "text": _LONG, "cfg": {"use_semantic_validation": True, "use_ner_layer": False},
     "presidio": [_rr("LOCATION", 41, 47, 0.9)], "port": {"enabled": False}},
    {"id": "validate_passthrough_only", "text": _LONG, "cfg": {"use_semantic_validation": True, "use_ner_layer": False},
     "presidio": [_rr("IBAN", 0, 5, 0.9)], "port": {"validate": "none"}},
    {"id": "aliases_short_text", "text": "Ahmet is here", "cfg": {"use_semantic_detection": True, "use_ner_layer": False},
     "port": {"aliases": [DetectedSpan(0, 5, "PERSON_NAME", 0.8)]}},
    {"id": "aliases_long_text", "text": _LONG, "cfg": {"use_semantic_detection": True, "use_ner_layer": False},
     "port": {"aliases": [DetectedSpan(0, 5, "PERSON_NAME", 0.8)]}},
    {"id": "aliases_raises", "text": _LONG, "cfg": {"use_semantic_detection": True, "use_ner_layer": False},
     "port": {"aliases_raises": True}},
    {"id": "contextual", "text": _LONG,
     "cfg": {"use_semantic_detection": True, "use_semantic_contextual_detection": True, "use_ner_layer": False},
     "port": {"semantic": [DetectedSpan(41, 47, "DIAGNOSIS", 0.8)]},
     "entity_types": ["DIAGNOSIS", "PERSON_NAME", "RELIGION"]},
    {"id": "contextual_no_types", "text": _LONG,
     "cfg": {"use_semantic_detection": True, "use_semantic_contextual_detection": True, "use_ner_layer": False},
     "port": {"semantic": [DetectedSpan(41, 47, "DIAGNOSIS", 0.8)]}, "entity_types": ["PERSON_NAME"]},
    {"id": "contextual_raises", "text": _LONG,
     "cfg": {"use_semantic_detection": True, "use_semantic_contextual_detection": True, "use_ner_layer": False},
     "port": {"semantic_raises": True}, "entity_types": ["DIAGNOSIS"]},
    {"id": "contextual_off", "text": _LONG,
     "cfg": {"use_semantic_detection": True, "use_ner_layer": False},
     "port": {"semantic": [DetectedSpan(41, 47, "DIAGNOSIS", 0.8)]}, "entity_types": ["DIAGNOSIS"]},
    {"id": "coref_runs", "text": _LONG,
     "cfg": {"use_semantic_detection": True, "use_ner_layer": False},
     "presidio": [_rr("PHONE_NUMBER", 0, 5, 0.8)], "port": {}},
    {"id": "coref_raises", "text": _LONG,
     "cfg": {"use_semantic_detection": True, "use_ner_layer": False},
     "presidio": [_rr("PHONE_NUMBER", 0, 5, 0.8)], "port": {"coref_raises": True}},
    {"id": "non_pii_filter", "text": _LONG, "cfg": {"use_ner_layer": False},
     "presidio": [_rr("PHONE_NUMBER", 0, 5, 0.8), _rr("LOCATION", 41, 47, 0.9)],
     "filter": ("LOCATION",)},
    {"id": "non_pii_filter_all", "text": _LONG, "cfg": {"use_ner_layer": False},
     "presidio": [_rr("LOCATION", 41, 47, 0.9)], "filter": ("LOCATION",)},
    {"id": "non_pii_filter_dup_spans", "text": _LONG, "cfg": {"use_ner_layer": False},
     "presidio": [_rr("LOCATION", 41, 47, 0.9), _rr("LOCATION", 41, 47, 0.9),
                  _rr("PHONE_NUMBER", 0, 5, 0.8)],
     "filter": ("DATE",)},
    {"id": "stopword_span", "text": "Hello there Ahmet Yılmaz", "cfg": {"use_ner_layer": False},
     "presidio": [_rr("PERSON_NAME", 0, 5, 0.9), _rr("PERSON_NAME", 12, 24, 0.9)]},
    {"id": "person_expand_merge", "text": "Ahmet Yılmaz Kaya\nMaria Garcia", "cfg": {"use_presidio_layer": False},
     "ner": [{"Ahmet": "PER", "Maria": "PER"}]},
    {"id": "low_confidence_logged", "text": _LONG, "cfg": {"use_ner_layer": False},
     "presidio": [_rr("PHONE_NUMBER", 0, 5, 0.4), _rr("LOCATION", 41, 47, 0.74)]},
    {"id": "overlap_dedupe", "text": _LONG, "cfg": {"use_ner_layer": False},
     "presidio": [_rr("PHONE_NUMBER", 0, 12, 0.8), _rr("NATIONAL_ID", 3, 14, 0.6), _rr("LOCATION", 10, 20, 0.9)]},
]


def _run_sanitize(scenario: dict) -> dict:
    cfg = SeptumCoreConfig(**scenario.get("cfg", {}))
    pipelines = [_Pipeline(n) for n in scenario.get("ner", [])]
    port = _Port(**scenario["port"]) if "port" in scenario else None
    detector = Detector.__new__(Detector)
    detector._config = cfg
    detector._ner_registry = _Registry(pipelines)
    detector._semantic_port = port or NullSemanticDetectionPort()
    detector._analyzer = _Analyzer(scenario.get("presidio", []))
    detector._entity_types = scenario.get("entity_types")
    detector._non_pii_filter = _Filter(scenario["filter"]) if "filter" in scenario else None

    handler = _Capture()
    logger = logging.getLogger("septum_core.detector")
    previous = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        anon_map = AnonymizationMap(document_id=0, language="en")
        result = detector.sanitize(scenario["text"], "en", anon_map)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)
    return {
        "text": result.sanitized_text,
        "count": result.entity_count,
        "types": result.entity_type_counts,
        "spans": [
            (s.start, s.end, s.entity_type, s.placeholder, s.score, s.core)
            for s in result.detected_spans
        ],
        "ner_inputs": [p.seen for p in pipelines],
        "analyzer_calls": detector._analyzer.calls,
        "port_calls": port.calls if port else None,
        "logs": handler.records,
    }


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(f"{record.levelname}:{record.getMessage()}")


def _sanitize_results() -> dict[str, dict]:
    return {s["id"]: _run_sanitize(s) for s in _SANITIZE_SCENARIOS}


def compute() -> dict:
    """Every pinned output, JSON-serialisable."""
    golden: dict = {}
    golden.update(_span_processing_digests())
    golden.update(_ner_digests())
    golden.update(_presidio_filter_digests())
    golden.update(_address_digests())
    golden["sanitize"] = json.loads(json.dumps(_sanitize_results()))
    return golden


_GOLDEN = json.loads(_GOLDEN_PATH.read_text(encoding="utf-8")) if _GOLDEN_PATH.exists() else {}

_STAGES = (
    "absorb", "absorb_raw", "dedupe", "dedupe_raw", "expand", "merge", "chain",
    "ner_merge", "ner_convert", "presidio_filter", "address",
)


@pytest.fixture(scope="module")
def computed() -> dict:
    return compute()


@pytest.mark.parametrize("stage", _STAGES)
def test_stage_digests_are_unchanged(computed, stage):
    assert computed[stage] == _GOLDEN[stage]


@pytest.mark.parametrize("scenario_id", [s["id"] for s in _SANITIZE_SCENARIOS])
def test_sanitize_scenario_is_unchanged(computed, scenario_id):
    assert computed["sanitize"][scenario_id] == _GOLDEN["sanitize"][scenario_id]


if __name__ == "__main__":
    Path(sys.argv[1]).write_text(
        json.dumps(compute(), ensure_ascii=False, indent=0), encoding="utf-8"
    )
