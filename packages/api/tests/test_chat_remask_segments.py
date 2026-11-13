"""Chat re-mask of an outdated document's chunks, per language segment.

Ingestion masks a mixed-language document per language segment; the chat
re-mask of its stored chunks must give each chunk the same treatment, unless
the user chose the document's language, within one budget for all the chunks
of a call. The sanitizer is a recording double, except in the one test that
runs core's detector with stub NER pipelines: no model and no network call runs.
"""

from __future__ import annotations

import copy
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator, Iterator

import pytest
from langdetect import DetectorFactory
from septum_api.routers import chat as chat_module
from septum_api.services import document_anon_store, pii_gate, policy_snapshot
from septum_api.services.anonymization_map import AnonymizationMap
from septum_api.services.pii_gate import TextSegmenter
from septum_api.services.remask_cache import remask_cache
from septum_api.utils import db_helpers
from septum_api.utils.db_helpers import effective_masking_source, masking_segments
from septum_core.config import SeptumCoreConfig
from septum_core.detector import Detector
from septum_core.language_segments import Segment
from septum_core.ner_model_registry import NERModelRegistry, _WindowedNERPipeline
from septum_core.spans import ResolvedSpan, SanitizeResult

EN = (
    "The quarterly report describes the ongoing negotiations between both parties and the "
    "obligations each of them accepts under the agreement. "
) * 4
ZH = "本合同由双方根据平等自愿的原则订立，双方应当严格履行合同约定的各项义务，不得擅自变更。" * 3
NAME = "Alice Doe"
KNOWN_ORIGINAL = "Robert Example"
CUSTOM_ORIGINAL = "Kappa Theta"
DOC_ID = 7


class _RecordingSanitizer:
    """Ungated sanitizer double that finds :data:`NAME` and records how it was called."""

    context_gate = False

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, Any]] = []

    @staticmethod
    def _result(text: str) -> SanitizeResult:
        start = text.find(NAME)
        spans = [] if start == -1 else [ResolvedSpan(start, start + len(NAME), "PERSON_NAME", "", 1.0)]
        return SanitizeResult(sanitized_text=text, entity_count=len(spans), detected_spans=spans)

    def sanitize(self, text: str, language: str, anon_map: Any) -> SanitizeResult:
        self.calls.append(("sanitize", text, language, None))
        return self._result(text)

    def detect(self, text: str, language: str, *, segments: Any = None) -> Any:
        self.calls.append(("detect", text, language, segments))
        return SimpleNamespace(text=text)

    def apply(self, detection: Any, anon_map: Any) -> SanitizeResult:
        return self._result(detection.text)

    @property
    def kinds(self) -> list[str]:
        return [call[0] for call in self.calls]


@pytest.fixture(autouse=True)
def _seed_langdetect():
    DetectorFactory.seed = 42


@pytest.fixture
async def stored_map(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    async def _row_exists(_db, _document):
        return None

    monkeypatch.setattr(document_anon_store, "_ANON_MAP_DIR", tmp_path / "maps")
    monkeypatch.setattr(document_anon_store, "_document_maps", {})
    monkeypatch.setattr(pii_gate, "_require_row", _row_exists)
    anon_map = AnonymizationMap(document_id=DOC_ID, language="en")
    anon_map.add_entity(KNOWN_ORIGINAL, "PERSON_NAME")
    anon_map.add_entity(CUSTOM_ORIGINAL, "Project code")
    await document_anon_store.set_document_map(DOC_ID, anon_map)
    yield


@pytest.fixture
def clean_cache() -> Iterator[None]:
    remask_cache.clear()
    try:
        yield
    finally:
        remask_cache.clear()


def _document(detected: str = "en", override: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(id=DOC_ID, detected_language=detected, language_override=override)


async def _stored() -> AnonymizationMap:
    stored = await document_anon_store.get_document_map(DOC_ID)
    assert stored is not None
    return copy.deepcopy(stored)


async def _remask(
    document: SimpleNamespace, *texts: str, sanitizer: Any = None, segmenter: Any = None
) -> tuple[list[str], Any]:
    sanitizer = sanitizer or _RecordingSanitizer()
    masked, _ = await pii_gate.remask_document_texts(
        None, None, document, list(texts), sanitizer, segmenter=segmenter
    )
    return masked, sanitizer


async def _segments_of(document: SimpleNamespace, text: str) -> Any:
    _, sanitizer = await _remask(document, text)
    [(kind, detected_text, _, segments)] = sanitizer.calls
    return kind, detected_text, segments


# Mixed, single-language and override chunks.


async def test_mixed_language_chunk_reaches_the_detector_with_segments(stored_map) -> None:
    text = f"{KNOWN_ORIGINAL} met {NAME}. {EN}\n\n{ZH}"

    [masked], sanitizer = await _remask(_document(), text)

    [(kind, detected_text, language, segments)] = sanitizer.calls
    assert (kind, language) == ("detect", "en")
    zh_start = detected_text.index(ZH)
    assert segments == (Segment(0, zh_start, "en"), Segment(zh_start, len(detected_text), "zh"))
    assert KNOWN_ORIGINAL not in detected_text
    assert NAME not in masked and KNOWN_ORIGINAL not in masked
    stored = await document_anon_store.get_document_map(DOC_ID)
    assert stored is not None and NAME in stored.entity_map
    assert stored.language == "en"


async def test_chunk_joined_by_single_line_breaks_is_cut_at_the_line(stored_map) -> None:
    text = f"{EN}\n{ZH}"

    kind, detected_text, segments = await _segments_of(_document(), text)

    assert kind == "detect"
    assert segments == (Segment(0, len(EN) + 1, "en"), Segment(len(EN) + 1, len(text), "zh"))


async def test_three_segment_chunk_is_cut_on_both_sides_of_the_minority_block(stored_map) -> None:
    text = f"{EN}\n\n{ZH}\n\n{EN}"
    zh_start = len(EN) + 2
    zh_end = zh_start + len(ZH) + 2

    kind, _, segments = await _segments_of(_document(), text)

    assert kind == "detect"
    assert segments == (
        Segment(0, zh_start, "en"),
        Segment(zh_start, zh_end, "zh"),
        Segment(zh_end, len(text), "en"),
    )


async def test_a_short_stray_line_is_absorbed_and_the_chunk_stays_single_language(stored_map) -> None:
    kind, _, segments = await _segments_of(_document(), f"{EN}\n{ZH[:20]}\n{EN}")

    assert (kind, segments) == ("sanitize", None)


async def test_chunk_wholly_in_another_language_keeps_its_one_segment(stored_map) -> None:
    text = ZH * 2

    kind, _, segments = await _segments_of(_document(), text)

    assert (kind, segments) == ("detect", (Segment(0, len(text), "zh"),))


async def test_single_language_chunk_is_masked_exactly_as_without_segmentation(stored_map) -> None:
    text = f"{KNOWN_ORIGINAL} met {NAME}.\n{EN}\n\n{EN}"
    unsegmented = _RecordingSanitizer()
    [expected] = await pii_gate.gate_untrusted_texts(
        [text], await _stored(), None, None, language="en", sanitizer=unsegmented
    )

    [masked], sanitizer = await _remask(_document(), text)

    assert sanitizer.kinds == ["sanitize"]
    assert sanitizer.calls == unsegmented.calls
    assert masked == expected


async def test_a_regional_language_form_compares_by_its_iso_code(stored_map) -> None:
    sanitizer = _RecordingSanitizer()

    await pii_gate.gate_untrusted_texts(
        [ZH * 2], await _stored(), None, None,
        language="zh-CN", sanitizer=sanitizer, segmenter=TextSegmenter(),
    )

    assert sanitizer.kinds == ["sanitize"]


async def test_user_chosen_language_gets_no_segments_and_no_detection(stored_map, monkeypatch) -> None:
    def _no_detection(_sample):
        raise AssertionError("an override must not run language detection")

    def _no_view(*_args):
        raise AssertionError("an override must not build the language view")

    monkeypatch.setattr(db_helpers, "langdetect_candidates", _no_detection)
    monkeypatch.setattr(pii_gate, "_language_view", _no_view)

    kind, _, segments = await _segments_of(_document(override="en"), f"{EN}\n\n{ZH}")

    assert (kind, segments) == ("sanitize", None)


@pytest.mark.parametrize(
    ("override", "source"), [(None, "detected"), ("en", "override"), ("xx", "detected"), ("??", "detected")]
)
def test_effective_masking_source_trusts_only_a_supported_override(override, source) -> None:
    assert effective_masking_source(_document(override=override)) == source


# Placeholders.


def _placeholder_skew_text(token: str) -> str:
    listing = ", ".join([token] * 40)
    return f"{ZH}\n{listing}\n{ZH}"


def _line_starts(text: str) -> list[int]:
    return [0, *(i + 1 for i, ch in enumerate(text) if ch == "\n")]


async def test_standard_placeholders_do_not_open_a_segment(stored_map) -> None:
    text = _placeholder_skew_text("[PERSON_NAME_2] [EMAIL_ADDRESS_3]")
    raw = masking_segments(text, _line_starts(text), "zh", "detected")
    assert {s.language for s in raw} != {"zh"}, "the placeholders must skew an unblanked judgement"

    kind, _, segments = await _segments_of(_document(detected="zh"), text)

    assert (kind, segments) == ("sanitize", None)


async def test_known_custom_placeholders_are_blanked_through_the_map(stored_map) -> None:
    placeholder = (await _stored()).entity_map[CUSTOM_ORIGINAL]
    assert not pii_gate.PLACEHOLDER_PATTERN.fullmatch(placeholder)
    premasked = _placeholder_skew_text(placeholder)
    raw = masking_segments(premasked, _line_starts(premasked), "zh", "detected")
    assert {s.language for s in raw} != {"zh"}, "the placeholders must skew an unblanked judgement"

    kind, detected_text, segments = await _segments_of(
        _document(detected="zh"), _placeholder_skew_text(CUSTOM_ORIGINAL)
    )

    assert CUSTOM_ORIGINAL not in detected_text
    assert (kind, segments) == ("sanitize", None)


# Callers that never segment.


async def test_other_gate_callers_never_pass_segments(stored_map) -> None:
    text = f"{NAME} {EN}\n\n{ZH}"
    sanitizer = _RecordingSanitizer()

    await pii_gate.gate_document_texts(None, None, _document(), [text], sanitizer)
    await pii_gate.gate_untrusted_texts([text], await _stored(), None, None, sanitizer=sanitizer)
    await pii_gate._gate_and_merge(
        None, None, _document(), [text], sanitizer, require_existing_map=True
    )

    assert sanitizer.kinds == ["sanitize", "sanitize", "sanitize"]


@pytest.mark.parametrize("kwargs", [{"joined": True, "language": "en"}, {"language": None}])
async def test_segmented_gating_needs_a_language_and_separate_texts(kwargs) -> None:
    with pytest.raises(ValueError):
        await pii_gate.gate_untrusted_texts(
            ["text"], AnonymizationMap(document_id=1, language="en"), None, None,
            sanitizer=_RecordingSanitizer(), segmenter=TextSegmenter(), **kwargs,
        )


# The shared budget.


def _mixed_chunks(count: int, tail: str = "") -> list[str]:
    return [f"Clause {index}: {NAME} {EN}\n\n{ZH}{tail}" for index in range(count)]


def _budget_warning(text_number: int) -> str:
    return (
        f"Language segmentation budget spent on text {text_number} of the turn; "
        "later texts are masked in their dominant language"
    )


def _warnings(caplog) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == pii_gate.__name__ and r.levelno == logging.WARNING
    ]


@pytest.fixture
def langdetect_calls(monkeypatch) -> list[str]:
    calls: list[str] = []
    real = db_helpers.langdetect_candidates

    def _counted(sample):
        calls.append(sample)
        return real(sample)

    monkeypatch.setattr(db_helpers, "langdetect_candidates", _counted)
    return calls


@pytest.fixture
def map_changes_once(monkeypatch) -> None:
    real_same = pii_gate._same_map
    compared: list[bool] = []

    def _changed_once(a, b):
        if not compared:
            compared.append(True)
            return False
        return real_same(a, b)

    monkeypatch.setattr(pii_gate, "_same_map", _changed_once)


async def test_a_mixed_chunk_under_the_default_budget_logs_nothing(stored_map, caplog) -> None:
    with caplog.at_level(logging.WARNING):
        _, sanitizer = await _remask(_document(), *_mixed_chunks(2))

    assert sanitizer.kinds == ["detect", "detect"]
    assert _warnings(caplog) == []


async def test_segment_budget_is_shared_by_all_chunks_of_one_remask(
    stored_map, monkeypatch, caplog
) -> None:
    monkeypatch.setattr(pii_gate, "_MAX_SEGMENTS_PER_DOCUMENT", 4)
    chunks = _mixed_chunks(4)
    segmenter = TextSegmenter()

    with caplog.at_level(logging.WARNING):
        masked, sanitizer = await _remask(_document(), *chunks, segmenter=segmenter)

    assert sanitizer.kinds == ["detect", "detect", "sanitize", "sanitize"]
    assert all(NAME not in text for text in masked)
    assert segmenter.degraded == set(chunks[2:])
    assert _warnings(caplog) == [_budget_warning(2)]


@pytest.mark.parametrize(
    ("budget", "chunks", "kinds", "warned_on"),
    [
        (3, _mixed_chunks(3), ["detect", "sanitize", "sanitize"], 2),
        (2, _mixed_chunks(2, tail=f"\n\n{EN}"), ["sanitize", "sanitize"], 1),
    ],
)
async def test_chunk_whose_segments_overrun_the_budget_is_masked_in_one_language(
    stored_map, monkeypatch, caplog, budget, chunks, kinds, warned_on
) -> None:
    monkeypatch.setattr(pii_gate, "_MAX_SEGMENTS_PER_DOCUMENT", budget)
    segmenter = TextSegmenter()

    with caplog.at_level(logging.WARNING):
        masked, sanitizer = await _remask(_document(), *chunks, segmenter=segmenter)

    assert sanitizer.kinds == kinds
    assert all(NAME not in text for text in masked)
    assert segmenter.degraded == set(chunks[kinds.index("sanitize"):])
    assert _warnings(caplog) == [_budget_warning(warned_on)]


async def test_detector_call_budget_is_shared_by_all_chunks_of_one_remask(
    stored_map, monkeypatch, caplog, langdetect_calls
) -> None:
    monkeypatch.setattr(pii_gate, "MAX_DETECTOR_CALLS_PER_DOCUMENT", 2)

    with caplog.at_level(logging.WARNING):
        masked, sanitizer = await _remask(_document(), *_mixed_chunks(4))

    assert len(langdetect_calls) == 2
    assert sanitizer.kinds == ["detect", "detect", "sanitize", "sanitize"]
    assert all(NAME not in text for text in masked)
    assert _warnings(caplog) == [_budget_warning(2)]


async def test_detector_calls_running_out_part_way_leave_that_chunk_in_its_dominant_language(
    stored_map, monkeypatch, caplog, langdetect_calls
) -> None:
    monkeypatch.setattr(pii_gate, "MAX_DETECTOR_CALLS_PER_DOCUMENT", 3)
    chunks = _mixed_chunks(3, tail=f"\n\n{EN}")
    segmenter = TextSegmenter()

    with caplog.at_level(logging.WARNING):
        masked, sanitizer = await _remask(_document(), *chunks, segmenter=segmenter)

    assert len(langdetect_calls) == 3
    assert sanitizer.kinds == ["detect", "sanitize", "sanitize"]
    assert all(NAME not in text for text in masked)
    assert segmenter.degraded == set(chunks[1:])
    assert _warnings(caplog) == [_budget_warning(2)]


async def test_a_retried_merge_does_not_judge_the_same_text_again(
    stored_map, map_changes_once, langdetect_calls
) -> None:
    _, sanitizer = await _remask(_document(), f"{EN}\n\n{ZH}")

    assert len(langdetect_calls) == 1
    assert sanitizer.kinds == ["detect", "detect"]
    assert sanitizer.calls[0] == sanitizer.calls[1]


async def test_a_retry_after_the_budget_is_spent_repeats_the_first_attempt(
    stored_map, monkeypatch, caplog, map_changes_once
) -> None:
    monkeypatch.setattr(pii_gate, "_MAX_SEGMENTS_PER_DOCUMENT", 2)

    with caplog.at_level(logging.WARNING):
        _, sanitizer = await _remask(_document(), *_mixed_chunks(2))

    assert sanitizer.kinds == ["detect", "sanitize", "detect", "sanitize"]
    assert sanitizer.calls[:2] == sanitizer.calls[2:]
    assert _warnings(caplog) == [_budget_warning(1)]


async def test_a_retried_single_language_chunk_spends_nothing_more(
    stored_map, map_changes_once, langdetect_calls
) -> None:
    segmenter = TextSegmenter()
    budget = segmenter.calls_left

    _, sanitizer = await _remask(_document(), f"{EN}\n\n{EN}", segmenter=segmenter)

    assert sanitizer.kinds == ["sanitize", "sanitize"]
    assert len(langdetect_calls) == 2
    assert segmenter.calls_left == budget - 2
    assert segmenter.degraded == set()


# One budget per chat turn, and the remask cache.


class _Turn:
    """Runs ``_resanitize_outdated_chunks`` over stub policy reads, recording each turn's sanitizer."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.sanitizers: list[_RecordingSanitizer] = []
        snapshot = SimpleNamespace(cache_key="policy")

        async def _snapshot(_db):
            return snapshot

        class _Composer:
            async def compose_with_snapshot(self, _db, _settings):
                return None, snapshot

        async def _sanitizer(*_args, **_kwargs):
            sanitizer = _RecordingSanitizer()
            self.sanitizers.append(sanitizer)
            return sanitizer

        monkeypatch.setattr(chat_module, "current_policy_snapshot", _snapshot)
        monkeypatch.setattr(chat_module, "_outdated_documents", lambda docs, _snapshot: docs)
        monkeypatch.setattr(chat_module, "PolicyComposer", _Composer)
        monkeypatch.setattr(chat_module, "create_user_text_sanitizer", _sanitizer)
        self.snapshot = snapshot

    async def run(self, chunks: list[Any], documents: dict[int, Any]) -> dict[int, str]:
        restored, _ = await chat_module._resanitize_outdated_chunks(None, None, chunks, documents)
        return restored

    async def misses(self, document: Any, chunks: list[Any]) -> list[Any]:
        anon_map = await document_anon_store.get_document_map(document.id)
        return [
            c for c, _ in chat_module._take_cached_remasks(document, chunks, self.snapshot, anon_map, {})
        ]


def _chunk(chunk_id: int, document_id: int, text: str) -> SimpleNamespace:
    return SimpleNamespace(id=chunk_id, document_id=document_id, sanitized_text=text)


async def test_one_budget_spans_every_document_of_a_turn(
    stored_map, monkeypatch, clean_cache
) -> None:
    other = SimpleNamespace(id=DOC_ID + 1, detected_language="en", language_override=None)
    await document_anon_store.set_document_map(
        other.id, AnonymizationMap(document_id=other.id, language="en")
    )
    monkeypatch.setattr(pii_gate, "_MAX_SEGMENTS_PER_DOCUMENT", 2)
    first, second = _mixed_chunks(2)
    turn = _Turn(monkeypatch)

    restored = await turn.run(
        [_chunk(1, DOC_ID, first), _chunk(2, other.id, second)],
        {DOC_ID: _document(), other.id: other},
    )

    [sanitizer] = turn.sanitizers
    assert sanitizer.kinds == ["detect", "sanitize"]
    assert all(NAME not in text for text in restored.values())


async def test_chunk_left_unsegmented_by_a_spent_budget_is_not_cached(
    stored_map, monkeypatch, clean_cache
) -> None:
    monkeypatch.setattr(pii_gate, "_MAX_SEGMENTS_PER_DOCUMENT", 2)
    document = _document()
    chunks = [_chunk(index + 1, DOC_ID, text) for index, text in enumerate(_mixed_chunks(2))]
    turn = _Turn(monkeypatch)

    first = await turn.run(chunks, {DOC_ID: document})

    assert turn.sanitizers[0].kinds == ["detect", "sanitize"]
    assert all(NAME not in text for text in first.values())
    assert await turn.misses(document, chunks) == [chunks[1]]

    second = await turn.run(chunks, {DOC_ID: document})

    assert turn.sanitizers[1].kinds == ["detect"]
    assert second[chunks[0].id] == first[chunks[0].id]
    assert await turn.misses(document, chunks) == []


def test_cached_remask_is_reused_only_for_the_same_language_and_source(clean_cache) -> None:
    chunk = SimpleNamespace(id=1, sanitized_text=f"{EN}\n\n{ZH}")
    snapshot = SimpleNamespace(cache_key="policy")
    anon_map = AnonymizationMap(document_id=DOC_ID, language="en")
    detected = _document("en", None)

    [(_, key)] = chat_module._take_cached_remasks(detected, [chunk], snapshot, anon_map, {})
    remask_cache.put(key, "masked per segment")
    restored: dict[int, str] = {}
    assert chat_module._take_cached_remasks(detected, [chunk], snapshot, anon_map, restored) == []
    assert restored == {1: "masked per segment"}

    for other in (_document("en", "en"), _document("de", None), _document("en", "de")):
        assert len(chat_module._take_cached_remasks(other, [chunk], snapshot, anon_map, {})) == 1


def test_cache_key_requires_the_language_source() -> None:
    positional_only = (1, 1, "p", "t", "en")
    with pytest.raises(TypeError):
        remask_cache.key(*positional_only)


def test_policy_cache_key_carries_the_segmentation_revision(monkeypatch) -> None:
    before = policy_snapshot.build_policy_snapshot([["PERSON_NAME"]], [])
    monkeypatch.setattr(policy_snapshot, "LANGUAGE_SEGMENTATION_REVISION", "language-segments-next")
    after = policy_snapshot.build_policy_snapshot([["PERSON_NAME"]], [])

    assert before.cache_key != after.cache_key


# Core's detector, with stub NER pipelines.

EN_FILLER = "The committee reviewed the annual budget in detail. "
TR_FILLER = "Komite yıllık bütçeyi ayrıntılı olarak inceledi. "
EN_NAME = "Brown Smith"
TR_NAME = "Ayse Demir"
# Core's own stand-in for a token-classification pipeline; it lives with core's
# tests, so this one test is skipped in a checkout without them.
CORE_TEST_HELPERS = Path(__file__).resolve().parents[2] / "core" / "tests"


class _StubRegistry(NERModelRegistry):
    """Real model-set planning; each model's stub pipeline finds only the names it is given."""

    def __init__(self, emits: dict[str, dict[str, str]], make_pipeline: Any) -> None:
        super().__init__(_overrides={})
        self._emits = emits
        self._make_pipeline = make_pipeline

    def get_pipelines_for_models(self, model_ids):
        return [
            _WindowedNERPipeline(self._make_pipeline(emit=self._emits.get(mid, {})), None)
            for mid in model_ids
        ]


async def test_core_detector_masks_with_segments_everything_it_masks_without(
    stored_map, monkeypatch
) -> None:
    if not (CORE_TEST_HELPERS / "ner_fakes.py").is_file():
        pytest.skip("core's test helpers are not in this checkout")
    monkeypatch.syspath_prepend(str(CORE_TEST_HELPERS))
    ner_fakes = pytest.importorskip("ner_fakes")
    emits = {
        NERModelRegistry().model_ids("en")[0]: {EN_NAME: "PER"},
        NERModelRegistry().model_ids("tr")[0]: {TR_NAME: "PER"},
    }
    detector = Detector(
        SeptumCoreConfig(use_presidio_layer=False, use_ner_layer=True),
        ner_registry=_StubRegistry(emits, ner_fakes.FakePipeline),
        context_gate=False,
    )
    text = (
        EN_FILLER * 10 + f"Mr {EN_NAME} attended the meeting. " + EN_FILLER * 4
        + "\n\n"
        + TR_FILLER * 10 + f"Sayin {TR_NAME} toplantiya katildi. " + TR_FILLER * 4
    )
    single_map = await _stored()
    [single] = await pii_gate.gate_untrusted_texts(
        [text], single_map, None, None, language="en", sanitizer=detector
    )

    [segmented], _ = await _remask(_document(), text, sanitizer=detector)

    stored = await document_anon_store.get_document_map(DOC_ID)
    assert stored is not None
    assert EN_NAME not in single and TR_NAME in single
    assert EN_NAME not in segmented and TR_NAME not in segmented
    assert any(TR_NAME in original for original in stored.entity_map)
    assert set(single_map.entity_map) <= set(stored.entity_map)
