"""Pin the exact output of the registry, unmasker, non-PII filter, validated
recognizer, anonymization map and NER window planner.

A seeded corpus drives each target; every case is reduced to a digest and
compared with ``complexity_golden_2.json``. A refactor of these units must
leave every digest unchanged. Regenerate the golden file only for a
deliberate behaviour change: write it to a scratch path, review it, then
copy it over the golden::

    python tests/test_complexity_characterization_2.py <output.json>
    cp <output.json> tests/complexity_golden_2.json
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from presidio_analyzer import Pattern, PatternRecognizer

from septum_core.anonymization_map import AnonymizationMap
from septum_core.ner_windowing import NERWindowingError, _token_ranges, plan_windows
from septum_core.non_pii_filter import NonPiiFilter, SpanView
from septum_core.recognizers import registry as registry_module
from septum_core.recognizers.base_recognizer import (
    RegexPatternConfig,
    ValidatedPatternRecognizer,
)
from septum_core.recognizers.registry import RecognizerRegistry
from septum_core.unmasker import Unmasker

_GOLDEN_PATH = Path(__file__).parent / "complexity_golden_2.json"


def _digest(value: object) -> str:
    return hashlib.sha1(repr(value).encode("utf-8")).hexdigest()[:12]


class _LogCapture(logging.Handler):
    def __init__(self, logger: logging.Logger) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[str] = []
        self._logger = logger

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(f"{record.levelname}:{record.getMessage()}")

    def __enter__(self) -> "_LogCapture":
        self._previous = self._logger.level
        self._logger.setLevel(logging.DEBUG)
        self._logger.addHandler(self)
        return self

    def __exit__(self, *exc: object) -> None:
        self._logger.removeHandler(self)
        self._logger.setLevel(self._previous)


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------


def _recognizer_repr(recognizer: object) -> tuple:
    return (
        type(recognizer).__name__,
        recognizer.name,
        tuple(recognizer.supported_entities),
        tuple((p.name, p.regex, p.score) for p in getattr(recognizer, "patterns", ())),
        tuple(getattr(recognizer, "deny_list", None) or ()),
    )


def _fake_pack_recognizer(name: str, entity: str, regex: str) -> PatternRecognizer:
    return PatternRecognizer(
        supported_entity=entity,
        patterns=[Pattern(name=name, regex=regex, score=0.5)],
        name=name,
    )


def _fake_modules() -> dict[str, object]:
    shared = ("DUP", "EMAIL_ADDRESS", r"\bdup\b")
    return {
        "a": SimpleNamespace(
            get_recognizers=lambda: [
                _fake_pack_recognizer("a1", "EMAIL_ADDRESS", r"\ba1\b"),
                _fake_pack_recognizer(*shared),
            ]
        ),
        "b": SimpleNamespace(
            get_recognizers=lambda: [
                _fake_pack_recognizer(*shared),
                _fake_pack_recognizer("b1", "PHONE_NUMBER", r"\bb1\b"),
                _fake_pack_recognizer("b1", "PHONE_NUMBER", r"\bb1\b"),
            ]
        ),
        "nofn": SimpleNamespace(),
        "boom": SimpleNamespace(get_recognizers=_raise_runtime),
        "opaque": SimpleNamespace(
            get_recognizers=lambda: [_Opaque(), _Opaque()]
        ),
    }


def _raise_runtime() -> list:
    raise RuntimeError("pack failure")


class _Opaque:
    """A recognizer whose dedup key cannot be computed."""

    name = "opaque"

    @property
    def supported_entities(self) -> list:
        raise TypeError("opaque")


def _load_builtin_results() -> dict:
    modules = _fake_modules()

    def fake_import(path: str):
        key = path.split(".")[-2]
        if key not in modules:
            raise ModuleNotFoundError(path)
        return modules[key]

    out: dict = {}
    registry = RecognizerRegistry()
    with _LogCapture(registry_module.logger) as cap:
        with mock.patch.object(registry_module.importlib, "import_module", fake_import):
            ids = ["a", "missing", "nofn", "boom", "b", "opaque", "a"]
            recs = registry._load_builtin_packs([SimpleNamespace(id=i) for i in ids])
    out["fake_packs"] = [
        [type(r).__name__, r.name] if isinstance(r, _Opaque) else list(_recognizer_repr(r))
        for r in recs
    ]
    out["fake_logs"] = cap.records

    real_ids = [
        "gdpr", "kvkk", "hipaa", "ccpa", "lgpd", "pipl", "no_such_regulation",
        "gdpr", "uk_gdpr", "appi",
    ]
    with _LogCapture(registry_module.logger) as cap:
        recs = registry._load_builtin_packs([SimpleNamespace(id=i) for i in real_ids])
    out["real_packs"] = {
        "count": len(recs),
        "digest": _digest([_recognizer_repr(r) for r in recs]),
        "logs": cap.records,
    }
    return out


def _custom(**kwargs: object) -> SimpleNamespace:
    base = dict(
        id=1, name="c", entity_type="CUSTOM", detection_method="regex",
        pattern=None, keywords=None, llm_prompt=None, context_words=[],
        placeholder_label="C", is_active=True,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _custom_cases() -> list[SimpleNamespace]:
    return [
        _custom(id=1, name="rx", pattern=r"\d{3}"),
        _custom(id=2, name="inactive", pattern=r"\d+", is_active=False),
        _custom(id=3, name="badrx", pattern="[unclosed"),
        _custom(id=4, name="emptyrx", pattern=""),
        _custom(id=5, name="kw", detection_method="keyword_list", keywords=["a.b", "c"]),
        _custom(id=6, name="kwnone", detection_method="KEYWORD_LIST", keywords=[]),
        _custom(id=7, name="llm", detection_method="llm_prompt", llm_prompt="find x"),
        _custom(id=8, name="llmempty", detection_method="llm_prompt", llm_prompt=""),
        _custom(id=9, name="llmboom", detection_method="LLM_Prompt", llm_prompt="boom"),
        _custom(id=10, name="llmnone", detection_method="llm_prompt", llm_prompt="none"),
        _custom(id=11, name="unknown", detection_method="other", pattern="x"),
        _custom(id=12, name="rx2", detection_method="REGEX", pattern=r"[a-z]+"),
    ]


def _llm_factory(custom: SimpleNamespace):
    if custom.llm_prompt == "boom":
        raise RuntimeError("factory failure")
    if custom.llm_prompt == "none":
        return None
    return _fake_pack_recognizer(f"llm_{custom.name}", custom.entity_type, r"\bllm\b")


def _custom_results() -> dict:
    out: dict = {}
    for label, factory in (("no_factory", None), ("factory", _llm_factory)):
        registry = RecognizerRegistry(llm_recognizer_factory=factory)
        with _LogCapture(registry_module.logger) as cap:
            recs = registry._from_custom_recognizers(_custom_cases())
        out[label] = {
            "recs": [list(_recognizer_repr(r)) for r in recs],
            "logs": cap.records,
        }
    registry = RecognizerRegistry(llm_recognizer_factory=_llm_factory)
    with _LogCapture(registry_module.logger):
        built = registry.build([], _custom_cases())
    out["build_names"] = [r.name for r in built]
    out["empty"] = RecognizerRegistry()._from_custom_recognizers([]) == []
    return out


# --------------------------------------------------------------------------
# Unmasker
# --------------------------------------------------------------------------

_PLACEHOLDERS = (
    "[PERSON_NAME_1]", "[PERSON_NAME_2]", "[PERSON_1]", "[PERSON_2]",
    "[ORGANIZATION_NAME_1]", "[ORGANIZATION_1]", "[ORGANIZATION_NAME_3]",
    "[EMAIL_ADDRESS_1]", "[LOCATION_1]", "[PERSON_NAME_10]", "[PERSON_10]", "",
)
_ORIGINALS = (
    "Ali Kaya", "Ali", "Ali Kaya’nın", "ACME", "x@y.zz", "Ankara", "İstanbul",
    "Zoë", "ÇAĞRI Bey", "[PERSON_1]", "",
)
_FILLERS = ("hello", " ", ", ", "\n", "and", "ünal", "[UNKNOWN_9]", "[PERSON_NAME_x]")


def _unmask_cases(count: int = 700) -> list[tuple]:
    rng = random.Random(20261030)
    cases = []
    for _ in range(count):
        pairs = [
            (rng.choice(_ORIGINALS), rng.choice(_PLACEHOLDERS))
            for _ in range(rng.randint(0, 5))
        ]
        use_lookup = rng.random() < 0.4
        text = "".join(
            rng.choice(_PLACEHOLDERS if rng.random() < 0.6 else _FILLERS)
            for _ in range(rng.randint(0, 8))
        )
        allowed = None
        mode = rng.random()
        if mode < 0.35:
            allowed = frozenset(
                p for p in _PLACEHOLDERS if rng.random() < 0.5
            )
        cases.append((pairs, use_lookup, text, allowed))
    return cases


def _unmask_results() -> list:
    unmasker = Unmasker()
    results = []
    for pairs, use_lookup, text, allowed in _unmask_cases():
        amap = AnonymizationMap(document_id=1)
        if use_lookup:
            amap.placeholder_lookup = {p: o for o, p in pairs}
        else:
            amap.entity_map = {o: p for o, p in pairs}
        results.append(unmasker.unmask(text, amap, allowed))
    return results


# --------------------------------------------------------------------------
# NonPiiFilter
# --------------------------------------------------------------------------

_RULE_WORDS = ("hello", "Merhaba", "dear", "STRASSE", "straße", "a.b", "[x", r"\d+", "^ab")
_RULE_LANGS = ("en", "tr", "de", "EN", "xx")
_RULE_TYPES = ("PERSON_NAME", "LOCATION", "DATE")
_SPAN_WORDS = (
    "hello", "Hello", "MERHABA", "dear", "Straße", "STRASSE", "123", "ab", "a.b",
    "the", "  ", "", "İstanbul", "x", "Ali Kaya",
)


def _filter_cases(count: int = 500) -> list[tuple]:
    rng = random.Random(20261031)
    cases = []
    for _ in range(count):
        rules = []
        for _ in range(rng.randint(0, 4)):
            rules.append(
                SimpleNamespace(
                    pattern_type=rng.choice(("token", "regex", "TOKEN", " Regex ", "bogus", None)),
                    pattern=rng.choice(_RULE_WORDS + ("",)),
                    languages=rng.choice(([], None, [rng.choice(_RULE_LANGS)], list(_RULE_LANGS[:2]))),
                    entity_types=rng.choice(([], None, [rng.choice(_RULE_TYPES)])),
                    min_score=rng.choice((None, 0.5, 0.9)),
                    is_active=rng.random() < 0.9,
                )
            )
        parts = []
        spans = []
        pos = 0
        for _ in range(rng.randint(0, 5)):
            word = rng.choice(_SPAN_WORDS)
            spans.append(
                SpanView(pos, pos + len(word), rng.choice(_RULE_TYPES), rng.choice((0.3, 0.5, 0.7, 0.95)))
            )
            parts.append(word)
            pos += len(word)
            parts.append(" ")
            pos += 1
        language = rng.choice(("en", "tr", "de", "EN", " tr ", "", None, "xx"))
        cases.append((rules, "".join(parts), language, spans))
    return cases


def _filter_results() -> list:
    results = []
    for rules, text, language, spans in _filter_cases():
        flt = NonPiiFilter(rules)
        kept = flt.filter_spans(text, language, list(spans))
        results.append([(s.start, s.end, s.entity_type, s.score) for s in kept])
        results.append(NonPiiFilter.from_rules(rules) is None)
    return results


# --------------------------------------------------------------------------
# ValidatedPatternRecognizer
# --------------------------------------------------------------------------


def _validator_even(value: str) -> bool:
    return len(value) % 2 == 0


def _validator_boom(value: str) -> bool:
    raise ValueError("bad")


_RECOGNIZER_TEXTS = (
    "no: 1234 and No: 12345 then 99 and 123456",
    "x No: 12 y No: 1234 z",
    "",
    "no digits here",
    "No: 7 No: 88 No: 999 No: 4444",
)


def _recognizer_results() -> list:
    pattern_plain = r"\d{2,6}"
    pattern_group = r"(No:) (\d+)"
    pattern_optional = r"No:( \d+)?"
    configs = [
        (RegexPatternConfig("plain", pattern_plain), None),
        (RegexPatternConfig("plain_v", pattern_plain), _validator_even),
        (RegexPatternConfig("fb", pattern_plain, fallback_score=0.2), _validator_even),
        (RegexPatternConfig("fb_none", pattern_plain, fallback_score=0.2), None),
        (RegexPatternConfig("fb_boom", pattern_plain, fallback_score=0.2), _validator_boom),
        (RegexPatternConfig("grp", pattern_group, narrow_to_group=2), None),
        (RegexPatternConfig("grp_v", pattern_group, narrow_to_group=2), _validator_even),
        (RegexPatternConfig("grp_fb", pattern_group, narrow_to_group=2, fallback_score=0.1), _validator_even),
        (RegexPatternConfig("grp0", pattern_group, narrow_to_group=0), None),
        (RegexPatternConfig("grp_opt", pattern_optional, narrow_to_group=1), None),
        (RegexPatternConfig("boom_drop", pattern_plain), _validator_boom),
        (RegexPatternConfig("grp_boom", pattern_group, narrow_to_group=2), _validator_boom),
    ]
    results = []
    for config, validator in configs:
        recognizer = ValidatedPatternRecognizer(
            entity_type="THING", config=config, algorithmic_validator=validator
        )
        for text in _RECOGNIZER_TEXTS:
            for entities in ([], ["THING"], ["OTHER"], ["OTHER", "THING"]):
                found = recognizer.analyze(text, list(entities))
                results.append(
                    [(r.entity_type, r.start, r.end, r.score) for r in found]
                )
    return results


# --------------------------------------------------------------------------
# AnonymizationMap
# --------------------------------------------------------------------------

_MAP_VALUES = (
    "John Smith", "John", "Smith", "Ahmet Kaya", "Ahmet's", "Kaya’nın", "sarah",
    "SARAH CONNOR", "Sarah", "The", "an", "Al", "O’Brien", "Zoë Ünal", "ÇAĞRI",
    "Ankara", "Ankara Merkez", "ACME Corp", "12 May 2020", "May 2020", "a@b.cc",
)
_MAP_TYPES = (
    "PERSON_NAME", "PERSON_NAME", "FIRST_NAME", "LAST_NAME", "ALIAS", "USERNAME",
    "LOCATION", "ORGANIZATION_NAME", "DATE", "EMAIL_ADDRESS",
)
_MAP_TEXTS = (
    "john met SARAH in Ankara", "The Smith and [PERSON_NAME_1] and kaya's car",
    "", "ahmet, AHMET; Ahmet’s [x y] zoë", "[unclosed and çağrı", "plain text 12 an the",
    "\n\tconnor\n", "Ünal ÜNAL ünal",
)


def _map_cases(count: int = 250) -> list[tuple]:
    rng = random.Random(20261101)
    cases = []
    for _ in range(count):
        entries = [
            (rng.choice(_MAP_VALUES), rng.choice(_MAP_TYPES))
            for _ in range(rng.randint(0, 7))
        ]
        language = rng.choice(("en", "tr", "de"))
        texts = [(rng.choice(_MAP_TEXTS), rng.choice((None, "en", "tr", "de", ""))) for _ in range(3)]
        cases.append((language, entries, texts))
    return cases


def _map_results() -> list:
    results = []
    for language, entries, texts in _map_cases():
        amap = AnonymizationMap(document_id=1, language=language)
        placeholders = [amap.add_entity(value, etype) for value, etype in entries]
        redacted = [amap.apply_blocklist(text, lang) for text, lang in texts]
        results.append(
            [
                placeholders,
                sorted(amap.entity_map.items()),
                sorted(amap.token_to_placeholder.items()),
                sorted(amap.blocklist),
                sorted(amap.token_counter.items()),
                redacted,
            ]
        )
    empty = AnonymizationMap(document_id=2)
    results.append([empty.apply_blocklist("anything", None), empty.apply_blocklist("", None)])
    return results


# --------------------------------------------------------------------------
# NER windowing
# --------------------------------------------------------------------------


def _ner_cases(count: int = 600) -> list[tuple]:
    rng = random.Random(20261102)
    cases = []
    for _ in range(count):
        n = rng.randint(1, 45)
        offsets = []
        pos = 0
        for _ in range(n):
            share = rng.random() < 0.2 and offsets
            start = offsets[-1][0] + 1 if share and offsets[-1][1] - offsets[-1][0] > 1 else pos
            end = max(start + 1, start + rng.randint(1, 4)) if not share else offsets[-1][1]
            if share and rng.random() < 0.5:
                end += rng.randint(0, 2)
            offsets.append((start, end))
            pos = max(pos, end) + rng.randint(0, 2)
        max_tokens = rng.randint(3, 12)
        overlap = rng.choice((None, 0, 1, (max_tokens - 1) // 2))
        cases.append((offsets, pos, max_tokens, overlap))
    return cases


def _ner_results() -> list:
    results = []
    for offsets, length, max_tokens, overlap in _ner_cases():
        try:
            windows = plan_windows(offsets, length, max_tokens, overlap)
            results.append([(w.char_start, w.char_end, w.first, w.last) for w in windows])
        except (NERWindowingError, ValueError) as exc:
            results.append(f"{type(exc).__name__}:{exc}")
        if len(offsets) > max_tokens:
            try:
                ov = overlap if overlap is not None else min(64, max_tokens // 3)
                if 0 <= ov <= (max_tokens - 1) // 2:
                    results.append(_token_ranges(offsets, max_tokens, ov))
            except NERWindowingError as exc:
                results.append(f"raw:{exc}")
    return results


# --------------------------------------------------------------------------


def compute() -> dict:
    """Every pinned output, JSON-serialisable."""
    golden: dict = {}
    golden["registry_builtin"] = json.loads(json.dumps(_load_builtin_results()))
    golden["registry_custom"] = json.loads(json.dumps(_custom_results()))
    golden["unmask"] = _digest(_unmask_results())
    golden["unmask_sample"] = _unmask_results()[:12]
    golden["non_pii_filter"] = _digest(_filter_results())
    golden["recognizer"] = _digest(_recognizer_results())
    golden["recognizer_sample"] = _recognizer_results()[:24]
    golden["anon_map"] = _digest(_map_results())
    golden["anon_map_sample"] = json.loads(json.dumps(_map_results()[:3]))
    golden["ner_windows"] = _digest(_ner_results())
    golden["ner_windows_sample"] = _ner_results()[:6]
    return json.loads(json.dumps(golden, ensure_ascii=False))


_GOLDEN = json.loads(_GOLDEN_PATH.read_text(encoding="utf-8")) if _GOLDEN_PATH.exists() else {}


@pytest.fixture(scope="module")
def computed() -> dict:
    return compute()


@pytest.mark.parametrize(
    "key",
    [
        "registry_builtin", "registry_custom", "unmask", "unmask_sample",
        "non_pii_filter", "recognizer", "recognizer_sample", "anon_map",
        "anon_map_sample", "ner_windows", "ner_windows_sample",
    ],
)
def test_output_is_unchanged(computed, key):
    assert computed[key] == _GOLDEN[key]


def test_corpus_exercises_every_branch():
    """Guard against a corpus that degenerates and pins nothing."""
    assert any("[" in r for r in _unmask_results())
    kept_sizes = {len(r) for r in _filter_results() if isinstance(r, list)}
    assert len(kept_sizes) > 2
    assert any(isinstance(r, str) for r in _ner_results())
    assert any(isinstance(r, list) and len(r) > 1 for r in _ner_results())


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(
            "usage: python tests/test_complexity_characterization_2.py <output.json>\n"
            "Write to a scratch path, review the diff, then copy over "
            "tests/complexity_golden_2.json."
        )
    Path(sys.argv[1]).write_text(
        json.dumps(compute(), ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
