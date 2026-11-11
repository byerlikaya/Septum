"""The Ollama contextual layer runs in production and refuses instead of failing open.

Ollama is never reached: the HTTP client factory of the Ollama client is
replaced. Covers wiring (setting to port), the production sanitizer path, the
coded refusal on every failure kind, the refusal at chat, route and ingestion,
slicing of long input, and masking of every word-bounded occurrence.
"""

from __future__ import annotations

import json
import re
import signal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from septum_api import main as main_module
from septum_api.database import build_default_app_settings, get_db
from septum_api.models import Base
from septum_api.models.document import Chunk, Document
from septum_api.models.regulation import RegulationRuleset
from septum_api.models.settings import AppSettings
from septum_api.models.user import User
from septum_api.routers import chat as chat_module
from septum_api.routers import documents as documents_module
from septum_api.services import document_anon_store, ollama_client
from septum_api.services.anonymization_map import AnonymizationMap
from septum_api.services.contextual_placement import (
    MIN_SLICE_BYTES,
    contextual_slices,
)
from septum_api.services.ingestion.base import IngestionResult
from septum_api.services.llm_router import LLMRouter
from septum_api.services.ollama_detection_runner import clear_detection_cache
from septum_api.services.policy_composer import PolicyComposer
from septum_api.services.policy_snapshot import build_policy_snapshot
from septum_api.services.remask_cache import remask_cache
from septum_api.services.sanitizer import OllamaSemanticAdapter, PIISanitizer
from septum_api.services.sanitizer_factory import (
    DOCUMENT_SANITIZER_ENABLE_OLLAMA,
    create_sanitizer,
)
from septum_api.utils.auth_dependency import get_current_user
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

CODE = "semantic_layer_unavailable"
VALUE = "Type 2 diabetes"
FOUND = json.dumps([{"text": VALUE, "type": "DIAGNOSIS"}])
SENTENCE = f"The patient has {VALUE}."
SLICE_LIMIT = 2000
MEASURED_PROMPT_TOKENS = 600
TRAINED_CONTEXT = 131072
SHORT_CONTEXT = 5632
TOO_SHORT_CONTEXT = 4096


class _FakeOllama:
    """Stands in for the Ollama HTTP client; records each prompt."""

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.payloads: list[dict] = []
        self.error: Exception | None = None
        self.reply: str | Callable[[str], str] = FOUND
        self.extra: dict[str, Any] = {"prompt_eval_count": MEASURED_PROMPT_TOKENS}
        self.trained_context = TRAINED_CONTEXT
        self.show_body: Any = None
        self.show_status = 200
        self.show_requests: list[tuple[str, dict]] = []

    def __call__(self, timeout: Any) -> "_FakeOllama":
        return self

    def __enter__(self) -> "_FakeOllama":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def _show(self, url: str, json: dict) -> httpx.Response:
        self.show_requests.append((url, json))
        body = self.show_body
        if body is None:
            body = {
                "model_info": {
                    "general.architecture": "llama",
                    "llama.context_length": self.trained_context,
                }
            }
        return httpx.Response(self.show_status, json=body, request=httpx.Request("POST", url))

    def post(self, url: str, json: dict) -> httpx.Response:
        if url.endswith("/api/show"):
            return self._show(url, json)
        self.prompts.append(json["prompt"])
        self.payloads.append(json)
        if self.error is not None:
            raise self.error
        reply = self.reply(json["prompt"]) if callable(self.reply) else self.reply
        return httpx.Response(
            200,
            json={"response": reply, **self.extra},
            request=httpx.Request("POST", url),
        )


class _AsyncFakeOllama:
    """The async face of a :class:`_FakeOllama`: same routing, same records."""

    def __init__(self, fake: _FakeOllama) -> None:
        self._fake = fake

    def __call__(self, timeout: Any) -> "_AsyncFakeOllama":
        return self

    async def __aenter__(self) -> "_AsyncFakeOllama":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def post(self, url: str, json: dict) -> httpx.Response:
        return self._fake.post(url, json)


@pytest.fixture
def ollama(monkeypatch: pytest.MonkeyPatch) -> _FakeOllama:
    fake = _FakeOllama()
    monkeypatch.setattr(ollama_client, "ollama_http_client", fake)
    monkeypatch.setattr(ollama_client, "ollama_async_http_client", _AsyncFakeOllama(fake))
    return fake


def _settings(**overrides: Any) -> AppSettings:
    fields: dict[str, Any] = dict(
        id=1,
        use_presidio_layer=True,
        use_ner_layer=False,
        use_ollama_validation_layer=False,
        use_ollama_semantic_layer=True,
        use_ollama_layer=False,
        ollama_base_url="http://localhost:11500",
        ollama_deanon_model="ctx-model:1b",
    )
    fields.update(overrides)
    return AppSettings(**fields)


def _clinical_regulation() -> RegulationRuleset:
    return RegulationRuleset(
        id="hipaa",
        display_name="HIPAA",
        region="US",
        entity_types=["DIAGNOSIS", "MEDICATION"],
        is_builtin=False,
        is_active=True,
    )


async def _store(env: Any, *rows: Any) -> None:
    async with env.session_maker() as session:
        session.add_all(list(rows))
        await session.commit()


async def _sanitizer(env: Any, settings: AppSettings) -> PIISanitizer:
    await _store(env, _clinical_regulation())
    async with env.session_maker() as session:
        policy = await PolicyComposer().compose(session, settings)
    return PIISanitizer(settings=settings, policy=policy, enable_ollama_layer=False)


def _sanitize(sanitizer: PIISanitizer, text: str) -> str:
    return sanitizer.sanitize(text, "en", AnonymizationMap(document_id=1, language="en")).sanitized_text


def _adapter(settings: AppSettings | None = None) -> OllamaSemanticAdapter:
    return OllamaSemanticAdapter(settings=settings or _settings(), enabled=True)


def _detect(text: str, settings: AppSettings | None = None) -> list[Any]:
    return _adapter(settings).detect_semantic(normalized_text=text, entity_types=["DIAGNOSIS"])


def _text_part(prompt: str) -> str:
    """The inspected text of a prompt: what its ``<data-…>`` delimiters enclose, before the repeated task."""
    block = prompt.split("Text:\n", 1)[1]
    match = re.fullmatch(
        r"<data-([0-9a-f]{16})>\n(.*)\n</data-\1>\n\nTask \(repeated\): .*", block, re.DOTALL
    )
    assert match is not None, "the text part is not one nonce-delimited data block"
    return match.group(2)


def _sent_every_slice(
    ollama: "_FakeOllama", text: str, entity_types: list[str], window: int
) -> bool:
    """Whether every distinct slice of ``text`` was sent; identical slices are sent once."""
    from septum_api.services.sanitizer import _contextual_slice_budget

    budget = _contextual_slice_budget(entity_types, window)
    expected = {piece for _, piece in contextual_slices(text, budget)}
    return {_text_part(p) for p in ollama.prompts} == expected


def _reply_with_whole_values_of(prompt: str) -> str:
    """The reply of a model that reports ``VALUE`` once when its text part holds it."""
    return FOUND if VALUE in _text_part(prompt) else "[]"


def _occurrences(text: str, value: str) -> list[tuple[int, int]]:
    found: list[tuple[int, int]] = []
    start = text.find(value)
    while start >= 0:
        found.append((start, start + len(value)))
        start = text.find(value, start + 1)
    return found


async def test_contextual_setting_alone_enables_the_adapter_port(env) -> None:
    settings = _settings(use_ollama_semantic_layer=True, use_ollama_layer=False)

    sanitizer = await _sanitizer(env, settings)

    assert sanitizer._semantic_port.is_enabled() is True


async def test_the_production_document_sanitizer_masks_a_contextual_value(env, ollama) -> None:
    await _store(env, _clinical_regulation())
    async with env.session_maker() as session:
        sanitizer = await create_sanitizer(
            session, _settings(), enable_ollama=DOCUMENT_SANITIZER_ENABLE_OLLAMA
        )

    masked = _sanitize(sanitizer, f"Note: {SENTENCE}")

    assert VALUE not in masked
    assert "[DIAGNOSIS_1]" in masked
    assert len(ollama.prompts) == 1


def test_the_two_local_model_errors_share_a_base() -> None:
    from septum_api.errors import (
        ContentFreeError,
        CustomRuleUnavailableError,
        LocalModelUnavailableError,
        SemanticLayerUnavailableError,
    )

    assert issubclass(CustomRuleUnavailableError, LocalModelUnavailableError)
    assert issubclass(SemanticLayerUnavailableError, LocalModelUnavailableError)
    assert issubclass(LocalModelUnavailableError, ContentFreeError)
    assert SemanticLayerUnavailableError.code == CODE


@pytest.mark.parametrize(
    "arrange",
    [
        lambda f, s: setattr(f, "error", httpx.ConnectError("refused")),
        lambda f, s: setattr(f, "reply", "I cannot help with that."),
        lambda f, s: (
            setattr(f, "reply", '[{"text": "Type 2 diab'),
            setattr(f, "extra", {"done_reason": "length"}),
        ),
        lambda f, s: (
            setattr(f, "reply", "[]"),
            setattr(f, "extra", {"done_reason": "length"}),
        ),
    ],
    ids=["call-error", "unparsable-reply", "truncated-reply", "truncated-empty-reply"],
)
def test_a_failed_contextual_call_raises_the_coded_error(
    ollama: _FakeOllama, arrange: Callable[[_FakeOllama, AppSettings], Any]
) -> None:
    from septum_api.errors import SemanticLayerUnavailableError

    arrange(ollama, _settings())

    with pytest.raises(SemanticLayerUnavailableError) as raised:
        _detect(SENTENCE)

    assert raised.value.code == CODE
    assert VALUE not in str(raised.value)


def test_a_refused_ollama_url_raises_the_coded_error_without_a_request(
    ollama: _FakeOllama,
) -> None:
    from septum_api.errors import SemanticLayerUnavailableError

    with pytest.raises(SemanticLayerUnavailableError):
        _detect(SENTENCE, _settings(ollama_base_url="http://203.0.113.5:11434"))

    assert ollama.prompts == []


def test_an_exactly_empty_reply_finds_nothing_and_raises_nothing(ollama: _FakeOllama) -> None:
    ollama.reply = "[]"

    assert _detect(SENTENCE) == []


async def test_a_failing_contextual_call_refuses_the_sanitizer_run(env, ollama) -> None:
    from septum_api.errors import SemanticLayerUnavailableError

    ollama.error = httpx.ReadTimeout("slow")
    sanitizer = await _sanitizer(env, _settings())

    with pytest.raises(SemanticLayerUnavailableError):
        _sanitize(sanitizer, SENTENCE)


def _stored_settings() -> AppSettings:
    """A complete settings row: Presidio and the contextual layer, approval off."""
    row = build_default_app_settings()
    row.use_presidio_layer = True
    row.use_ner_layer = False
    row.use_ollama_validation_layer = False
    row.use_ollama_semantic_layer = True
    row.use_ollama_layer = False
    row.require_approval = False
    row.ollama_base_url = "http://localhost:11500"
    row.ollama_deanon_model = "ctx-model:1b"
    return row


class _ChatEnv:
    def __init__(self) -> None:
        self.client: AsyncClient
        self.doc_id: int
        self.router_calls = 0


@pytest.fixture
async def chat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ollama: _FakeOllama):
    """Real route and sanitizer; only Ollama, retrieval and the cloud LLM are doubles."""
    from septum_api.main import app

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'contextual.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    state = _ChatEnv()

    async def _override_get_db():
        async with session_maker() as session:
            yield session

    chunk_text = "Notes: the visit was routine."
    async with session_maker() as session:
        user = User(email="owner@example.com", hashed_password="x", role="editor")
        session.add_all([user, _stored_settings(), _clinical_regulation()])
        await session.flush()
        doc = Document(
            filename="stored.pdf", original_filename="notes.pdf",
            file_type="application/pdf", file_format="pdf", detected_language="en",
            encrypted_path="/nonexistent", file_size_bytes=10, ocr_confidence=0.0,
            active_regulation_ids=[], ingestion_status="completed", chunk_count=1,
            sanitized_entity_types=[],
            sanitized_policy_digest=build_policy_snapshot([], []).digest,
            sanitized_language="en", user_id=user.id,
        )
        session.add(doc)
        await session.flush()
        session.add(
            Chunk(document_id=doc.id, index=0, sanitized_text=chunk_text, char_count=len(chunk_text))
        )
        await session.commit()
    state.doc_id = doc.id

    async def _retrieve(db, document_id, query, top_k, chunk_count, relevance_threshold):
        rows = await db.execute(select(Chunk).where(Chunk.document_id == document_id))
        return list(rows.scalars().all())

    async def _classify(**kwargs):
        return "search"

    async def _complete(self, messages, **kwargs):
        state.router_calls += 1
        return "ok"

    monkeypatch.setattr(document_anon_store, "_ANON_MAP_DIR", tmp_path / "maps")
    monkeypatch.setattr(document_anon_store, "_document_maps", {})
    monkeypatch.setattr(document_anon_store, "_map_versions", {})
    monkeypatch.setattr(chat_module, "_retrieve_chunks", _retrieve)
    monkeypatch.setattr(chat_module, "_classify_query_intent", _classify)
    monkeypatch.setattr(LLMRouter, "complete", _complete)
    remask_cache.clear()
    await document_anon_store.set_document_map(
        doc.id, AnonymizationMap(document_id=doc.id, language="en")
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        state.client = client
        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_current_user] = lambda: user
        try:
            yield state
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)
            remask_cache.clear()
            await engine.dispose()


async def test_chat_turn_streams_the_coded_error_when_the_contextual_layer_fails(
    chat: _ChatEnv, ollama: _FakeOllama
) -> None:
    ollama.error = httpx.ConnectError("refused")

    resp = await chat.client.post(
        "/api/chat/ask",
        json={"message": f"Does the patient have {VALUE}?", "document_ids": [chat.doc_id]},
    )

    assert resp.status_code == 200, resp.text
    events = [
        json.loads(line[len("data: "):])
        for line in resp.text.splitlines()
        if line.startswith("data: ")
    ]
    assert [e["code"] for e in events if e["type"] == "error"] == [CODE]
    assert not [e for e in events if e["type"] == "answer_chunk"]
    assert chat.router_calls == 0
    assert VALUE not in resp.text


async def test_analyze_query_route_answers_503_with_the_code(
    chat: _ChatEnv, ollama: _FakeOllama
) -> None:
    ollama.error = httpx.ConnectError("refused")

    resp = await chat.client.post(
        "/api/chat/analyze_query",
        json={"message": f"Does the patient have {VALUE}?", "document_ids": []},
    )

    assert resp.status_code == 503
    assert resp.json()["code"] == CODE
    assert VALUE not in resp.text


def test_the_handler_is_registered_for_the_error() -> None:
    from septum_api.errors import SemanticLayerUnavailableError

    handlers = main_module.app.exception_handlers
    assert any(cls in handlers for cls in SemanticLayerUnavailableError.__mro__)


async def test_ingestion_marks_the_document_failed_with_the_code(
    env, ollama: _FakeOllama, monkeypatch: pytest.MonkeyPatch
) -> None:
    ollama.error = httpx.ConnectError("refused")
    await _store(env, _stored_settings(), _clinical_regulation())
    doc_id, _ = await env.add_document(status="processing")

    async def _ingest(**kwargs: Any) -> Any:
        return IngestionResult(
            text=f"Status report. {SENTENCE} Follow-up next month.",
            confidence=1.0,
            metadata={},
        )

    monkeypatch.setattr(documents_module._INGESTION_ROUTER, "ingest", _ingest)
    source = documents_module._UploadedSource(env.tmp / "unused", "application/pdf", "pdf")

    await documents_module._run_upload_ingestion(doc_id, source, env.user.id)

    document = await env.document(doc_id)
    assert document is not None
    assert document.ingestion_status == "failed"
    assert document.ingestion_error_code == CODE
    assert VALUE not in (document.ingestion_error or "")


def test_long_input_is_sliced_so_no_prompt_text_part_exceeds_the_limit(
    ollama: _FakeOllama,
) -> None:
    ollama.reply = "[]"
    text = ("The quarterly review covered budgets and schedules. " * 400)[:20_000]

    _detect(text)

    assert len(ollama.prompts) > 1
    assert max(len(_text_part(p)) for p in ollama.prompts) <= SLICE_LIMIT


def test_a_span_in_the_last_slice_gets_its_absolute_offset(ollama: _FakeOllama) -> None:
    ollama.reply = _reply_with_whole_values_of
    filler = ("The quarterly review covered budgets and schedules. " * 400)[:20_000]
    text = f"{filler} {SENTENCE}"
    start = text.rfind(VALUE)

    spans = _detect(text)

    assert [(s.start, s.end, s.entity_type) for s in spans] == [
        (start, start + len(VALUE), "DIAGNOSIS")
    ]
    assert text[spans[0].start : spans[0].end] == VALUE
    assert max(len(_text_part(p)) for p in ollama.prompts) <= SLICE_LIMIT


def test_a_value_across_a_slice_cut_is_found(ollama: _FakeOllama) -> None:
    """The value recurs every 17 characters, so every cut of the text lands inside one."""
    ollama.reply = _reply_with_whole_values_of
    text = f"{VALUE}. " * 1200
    expected = _occurrences(text, VALUE)

    spans = _detect(text)

    assert {(s.start, s.end) for s in spans} == set(expected)
    assert max(len(_text_part(p)) for p in ollama.prompts) <= SLICE_LIMIT


async def test_a_repeated_value_is_masked_at_every_occurrence(env, ollama) -> None:
    sanitizer = await _sanitizer(env, _settings())
    text = f"{SENTENCE} A year later the chart still lists {VALUE} as active."

    masked = _sanitize(sanitizer, text)

    assert VALUE not in masked
    assert masked.count("[DIAGNOSIS_1]") == 2


async def test_a_three_letter_value_is_masked(env, ollama) -> None:
    ollama.reply = json.dumps([{"text": "HIV", "type": "DIAGNOSIS"}])
    sanitizer = await _sanitizer(env, _settings())

    masked = _sanitize(sanitizer, "The patient is HIV positive.")

    assert "HIV" not in masked
    assert "[DIAGNOSIS_1]" in masked


async def test_an_occurrence_inside_a_longer_word_is_not_masked(env, ollama) -> None:
    ollama.reply = json.dumps([{"text": "HIV", "type": "DIAGNOSIS"}])
    sanitizer = await _sanitizer(env, _settings())

    masked = _sanitize(sanitizer, "The patient is HIV positive; see the ARCHIVE.")

    assert "is [DIAGNOSIS_1] positive" in masked
    assert "ARCHIVE" in masked


async def test_a_suffix_glued_occurrence_is_masked_as_its_whole_word(env, ollama) -> None:
    """Accepted over-masking: agglutinative suffixes look like any other glued letters, so HIVE goes too."""
    ollama.reply = json.dumps([{"text": "HIV", "type": "DIAGNOSIS"}])
    sanitizer = await _sanitizer(env, _settings())

    masked = _sanitize(sanitizer, "The patient is HIV positive and lives near the HIVE.")

    assert "HIV" not in masked


def _reply_with(value: str, entity_type: str = "DIAGNOSIS") -> str:
    return json.dumps([{"text": value, "type": entity_type}])


def _slice_ranges(text: str) -> list[tuple[int, int]]:
    return [(offset, offset + len(part)) for offset, part in contextual_slices(text)]


def test_slicing_a_million_characters_without_whitespace_terminates_and_covers_the_text() -> None:
    text = "a" * 1_000_000

    slices = contextual_slices(text)

    assert all(len(part) <= SLICE_LIMIT for _, part in slices)
    assert all(text[offset : offset + len(part)] == part for offset, part in slices)
    assert slices[0][0] == 0
    assert slices[-1][0] + len(slices[-1][1]) == len(text)
    assert all(nxt[0] > prev[0] for prev, nxt in zip(slices, slices[1:]))


@pytest.mark.parametrize("length", [1, 5, 2000, 2001, 100_000])
def test_slicing_whitespace_only_text_terminates_and_makes_progress(length: int) -> None:
    text = " " * length

    ranges = _slice_ranges(text)

    assert ranges[0][0] == 0
    assert ranges[-1][1] == length
    assert all(nxt[0] > prev[0] for prev, nxt in zip(ranges, ranges[1:]))


def test_whitespace_only_text_makes_no_model_call(ollama: _FakeOllama) -> None:
    assert _detect(" \n\t " * 3000) == []
    assert ollama.prompts == []


def test_slices_of_unspaced_text_overlap_by_exactly_the_overlap() -> None:
    ranges = _slice_ranges("a" * 10_000)

    assert {prev[1] - nxt[0] for prev, nxt in zip(ranges, ranges[1:])} == {200}


@pytest.mark.parametrize("text", ["abcdefg " * 1000, "a" * 9000, "lorem ipsum dolor " * 700])
def test_every_200_character_window_lies_whole_in_one_slice(text: str) -> None:
    ranges = _slice_ranges(text)
    window = 200

    uncovered = [
        a
        for a in range(len(text) - window + 1)
        if not any(lo <= a and a + window <= hi for lo, hi in ranges)
    ]

    assert uncovered == []


def test_values_exactly_at_a_slice_start_and_slice_end_are_found_once(
    ollama: _FakeOllama,
) -> None:
    value = "Anaemia"
    base = "abcdefg " * 1000
    ranges = _slice_ranges(base)
    starts_at = ranges[1][0]
    ends_at = ranges[0][1]
    chars = list(base)
    for begin in (starts_at, ends_at - len(value)):
        chars[begin : begin + len(value)] = value
    text = "".join(chars)
    assert text[starts_at : starts_at + len(value)] == value
    assert text[ends_at - len(value) : ends_at] == value
    ollama.reply = lambda prompt: _reply_with(value) if value in _text_part(prompt) else "[]"

    spans = _detect(text)

    assert [(s.start, s.end) for s in spans] == _occurrences(text, value)


def test_offsets_stay_exact_across_cuts_in_combining_marks_emoji_and_expanding_lowercase(
    ollama: _FakeOllama,
) -> None:
    value = "Anaemia"
    filler = "café \U0001f600‍\U0001f525 İstanbul näive "
    text = f"{filler * 300}{value} {filler * 40}{value}. {filler * 150}{value}"
    ollama.reply = lambda prompt: _reply_with(value) if value in _text_part(prompt) else "[]"

    spans = _detect(text)

    assert len(contextual_slices(text)) > 3
    assert [(s.start, s.end) for s in spans] == _occurrences(text, value)
    assert all(text[s.start : s.end] == value for s in spans)


@pytest.mark.parametrize(
    "value, text",
    [
        ("糖尿病", "患者患有糖尿病需要治疗"),
        ("糖尿病", "糖尿病"),
        ("หืด", "ผู้ป่วยเป็นหืดมานาน"),
    ],
)
def test_a_short_value_the_model_locates_in_unspaced_script_is_masked(
    ollama: _FakeOllama, value: str, text: str
) -> None:
    ollama.reply = _reply_with(value)

    spans = _detect(text)

    assert [(s.start, s.end) for s in spans] == _occurrences(text, value)


async def test_the_production_sanitizer_masks_a_value_in_unspaced_script(env, ollama) -> None:
    ollama.reply = _reply_with("糖尿病")
    sanitizer = await _sanitizer(env, _settings())

    masked = _sanitize(sanitizer, "患者患有糖尿病需要治疗")

    assert "糖尿病" not in masked
    assert "[DIAGNOSIS_1]" in masked


@pytest.mark.parametrize("value", ["HIV", "AIDS", "ADHD"])
def test_a_short_all_caps_acronym_is_masked(ollama: _FakeOllama, value: str) -> None:
    ollama.reply = _reply_with(value)
    text = f"Diagnosis on file: {value} since last year."

    spans = _detect(text)

    assert [text[s.start : s.end] for s in spans] == [value]


def _detect_types(text: str, entity_types: list[str]) -> list[Any]:
    return _adapter().detect_semantic(normalized_text=text, entity_types=entity_types)


def _masked_texts(text: str, spans: list[Any]) -> list[str]:
    return [text[s.start : s.end] for s in spans]


@pytest.mark.parametrize(
    "value, entity_type",
    [
        ("DIABETES", "DIAGNOSIS"),
        ("ASTHMA", "DIAGNOSIS"),
        ("MUSLIM", "RELIGION"),
        ("COVID-19", "DIAGNOSIS"),
        ("TYPE 2 DIABETES", "DIAGNOSIS"),
        ("METFORMIN 500MG", "MEDICATION"),
    ],
)
def test_an_all_caps_value_is_masked(ollama: _FakeOllama, value: str, entity_type: str) -> None:
    ollama.reply = _reply_with(value, entity_type)
    text = f"Known on file: {value} since childhood."

    spans = _detect_types(text, [entity_type])

    assert _masked_texts(text, spans) == [value]


def test_an_all_caps_value_is_masked_on_its_own_line_too(ollama: _FakeOllama) -> None:
    """A list item fills its line exactly like a heading does, so line position cannot tell them apart."""
    text = "CONDITIONS\nASTHMA\nDIABETES\n"
    ollama.reply = json.dumps(
        [{"text": "ASTHMA", "type": "DIAGNOSIS"}, {"text": "DIABETES", "type": "DIAGNOSIS"}]
    )

    assert _masked_texts(text, _detect(text)) == ["ASTHMA", "DIABETES"]


@pytest.mark.parametrize("heading", ["DIAGNOSIS", "MEDICAL HISTORY", "2. Diagnosis"])
def test_a_heading_the_model_reports_as_a_value_is_masked(ollama: _FakeOllama, heading: str) -> None:
    """Accepted over-masking: a reported heading is masked rather than judged by its shape."""
    text = f"{heading}\nThe patient is stable and improving."
    ollama.reply = _reply_with(heading)

    assert _masked_texts(text, _detect(text)) == [heading]


def test_the_same_six_letter_value_is_masked_when_the_model_reports_it_in_lower_case(
    ollama: _FakeOllama,
) -> None:
    text = "Known conditions: ASTHMA since childhood."
    ollama.reply = _reply_with("asthma")

    spans = _detect(text)

    assert [text[s.start : s.end] for s in spans] == ["ASTHMA"]


@pytest.mark.parametrize(
    "value, text, entity_type",
    [
        ("السكري", "المريض يعاني من السكري منذ سنوات", "DIAGNOSIS"),
        ("مرض السكري", "تم تشخيص مرض السكري لديه", "DIAGNOSIS"),
        ("मधुमेह", "रोगी को मधुमेह है", "DIAGNOSIS"),
        ("מוסלמי", "הוא מוסלמי אדוק", "RELIGION"),
        ("慢性阻塞性肺疾病", "患者患有慢性阻塞性肺疾病需要治疗", "DIAGNOSIS"),
        ("เบาหวาน", "ผู้ป่วยเป็นเบาหวานมานาน", "DIAGNOSIS"),
    ],
)
def test_a_value_in_a_script_without_case_is_masked(
    ollama: _FakeOllama, value: str, text: str, entity_type: str
) -> None:
    """A script without case equals its own upper case; that never makes a value a heading."""
    ollama.reply = _reply_with(value, entity_type)

    spans = _detect_types(text, [entity_type])

    assert _masked_texts(text, spans) == [value]


@pytest.mark.parametrize(
    "reported, text, expected",
    [
        ("Ménière disease", "Seen for Ménière disease today.", "Ménière disease"),
        ("Type  2\n diabetes", "The patient has Type 2 diabetes.", "Type 2 diabetes"),
        ("Type 2 diabetes", "The patient has Type 2\ndiabetes.", "Type 2\ndiabetes"),
        ("SARILIK", "Hastada sarılık var.", "sarılık"),
        ("KALP YETMEZLİĞİ", "Tanı: kalp yetmezliği.", "kalp yetmezliği"),
        ("당뇨병", "진단은 당뇨병입니다.", "당뇨병입니다"),
        ("السكري", "المريض مصاب بالسكري منذ سنوات", "بالسكري"),
        ("diyabet", "Hasta diyabetli bir kadın.", "diyabetli"),
    ],
    ids=[
        "nfd", "model-whitespace", "text-line-break", "dotless-i", "dotted-capital-i",
        "particle", "prefix", "suffix",
    ],
)
def test_a_reported_value_is_placed_despite_normalization_spacing_casing_and_affixes(
    ollama: _FakeOllama, reported: str, text: str, expected: str
) -> None:
    ollama.reply = _reply_with(reported)

    spans = _detect(text)

    assert _masked_texts(text, spans) == [expected]


def test_a_value_buried_inside_a_longer_word_is_not_masked(ollama: _FakeOllama) -> None:
    ollama.reply = _reply_with("HIV")
    text = "The patient is HIV positive; see the ARCHIVE."

    assert _masked_texts(text, _detect(text)) == ["HIV"]


@pytest.mark.parametrize(
    "item, requested, expected_type",
    [
        ({"text": VALUE, "entity_type": "DIAGNOSIS"}, ["DIAGNOSIS"], "DIAGNOSIS"),
        ({"text": VALUE, "type": "DISEASE"}, ["DIAGNOSIS"], "DIAGNOSIS"),
        (VALUE, ["DIAGNOSIS"], "DIAGNOSIS"),
        ({"text": VALUE, "type": "medication"}, ["DIAGNOSIS", "MEDICATION"], "MEDICATION"),
        ({"text": VALUE, "type": "medications"}, ["DIAGNOSIS", "MEDICATION"], "DIAGNOSIS"),
        (
            {"text": VALUE, "type": "political opinion"},
            ["POLITICAL_OPINION", "RELIGION"],
            "POLITICAL_OPINION",
        ),
    ],
    ids=[
        "entity-type-key", "single-type-variant", "bare-string", "lower-case",
        "plural-falls-back-to-first", "spaced-label",
    ],
)
def test_a_variant_type_label_maps_to_a_requested_type(
    ollama: _FakeOllama, item: Any, requested: list[str], expected_type: str
) -> None:
    ollama.reply = json.dumps([item])

    spans = _detect_types(SENTENCE, requested)

    assert [(SENTENCE[s.start : s.end], s.entity_type) for s in spans] == [(VALUE, expected_type)]


@pytest.mark.parametrize(
    "reply",
    [
        _reply_with("Hypertension"),
        json.dumps([{"text": "Hypertension", "type": "SPORT"}]),
        json.dumps(["Hypertension"]),
    ],
    ids=["value-not-in-text", "unmappable-type", "untyped-with-two-requested"],
)
def test_a_reply_none_of_whose_values_can_be_placed_is_refused(
    ollama: _FakeOllama, reply: str, caplog: pytest.LogCaptureFixture
) -> None:
    from septum_api.errors import SemanticLayerUnavailableError

    ollama.reply = reply

    with caplog.at_level("DEBUG"), pytest.raises(SemanticLayerUnavailableError) as raised:
        _detect_types(SENTENCE, ["DIAGNOSIS", "MEDICATION"])

    assert raised.value.code == CODE
    assert "unplaceable_reply" in str(raised.value)
    assert VALUE not in caplog.text and "Hypertension" not in caplog.text


def test_unplaceable_values_beside_placed_ones_are_counted_in_a_content_free_log(
    ollama: _FakeOllama, caplog: pytest.LogCaptureFixture
) -> None:
    ollama.reply = json.dumps(
        [{"text": VALUE, "type": "DIAGNOSIS"}, {"text": "Hypertension", "type": "DIAGNOSIS"}]
    )

    with caplog.at_level("DEBUG"):
        spans = _detect(SENTENCE)

    assert _masked_texts(SENTENCE, spans) == [VALUE]
    assert "1 of 2" in caplog.text
    assert "Hypertension" not in caplog.text and VALUE not in caplog.text


def test_the_request_sets_the_context_window_explicitly(ollama: _FakeOllama) -> None:
    _detect(SENTENCE)

    options = ollama.payloads[0]["options"]
    assert options["num_ctx"] == ollama_client.OLLAMA_NUM_CTX
    assert options["num_predict"] == ollama_client.OLLAMA_NUM_PREDICT
    assert options["num_ctx"] > options["num_predict"]


def _overflowing_count() -> int:
    return ollama_client.OLLAMA_NUM_CTX - ollama_client.OLLAMA_NUM_PREDICT


@pytest.mark.parametrize(
    "extra",
    [
        lambda: {"prompt_eval_count": _overflowing_count()},
        lambda: {},
        lambda: {"prompt_eval_count": None},
        lambda: {"prompt_eval_count": "600"},
        lambda: {"prompt_eval_count": True},
    ],
    ids=["overflow", "missing", "null", "not-an-int", "bool"],
)
def test_a_prompt_the_server_may_have_cut_is_refused(
    ollama: _FakeOllama, extra: Callable[[], dict[str, Any]]
) -> None:
    from septum_api.errors import SemanticLayerUnavailableError

    ollama.extra = extra()

    with pytest.raises(SemanticLayerUnavailableError) as raised:
        _detect(SENTENCE)

    assert raised.value.code == CODE


def test_a_prompt_just_inside_the_window_is_answered(ollama: _FakeOllama) -> None:
    ollama.extra = {"prompt_eval_count": _overflowing_count() - 1}

    assert _masked_texts(SENTENCE, _detect(SENTENCE)) == [VALUE]


@pytest.mark.parametrize(
    "unit", ["患者患有糖尿病需要治疗", "ผู้ป่วยเป็นเบาหวานมานาน", "\U0001f600\U0001f525", "café näive "]
)
def test_every_contextual_prompt_fits_the_window_whatever_the_script(
    ollama: _FakeOllama, unit: str
) -> None:
    """A token covers at least one UTF-8 byte, so a prompt this many bytes long cannot overflow."""
    ollama.reply = "[]"
    text = unit * (12_000 // len(unit))

    types = ["DIAGNOSIS", "MEDICATION", "RELIGION"]
    _detect_types(text, types)

    worst = max(len(p.encode("utf-8")) for p in ollama.prompts)
    assert worst + ollama_client.OLLAMA_NUM_PREDICT < ollama_client.OLLAMA_NUM_CTX
    assert _sent_every_slice(ollama, text, types, ollama_client.OLLAMA_NUM_CTX)


_SEVERAL_TYPES = ["MEDICATION", "MEDICAL_CONDITION", "RELIGION"]


@pytest.mark.parametrize(
    "label, expected_type",
    [
        ("medication", "MEDICATION"),
        ("medical condition", "MEDICAL_CONDITION"),
        ("MEDICAL-CONDITION", "MEDICAL_CONDITION"),
        ("Religion", "RELIGION"),
        ("Religions", "MEDICATION"),
        ("medical conditions", "MEDICATION"),
        ("RELIGON", "MEDICATION"),
    ],
)
def test_a_label_names_a_type_only_up_to_case_and_spacing_else_the_first_requested_type_is_used(
    ollama: _FakeOllama, label: str, expected_type: str
) -> None:
    ollama.reply = json.dumps([{"text": VALUE, "type": label}])

    spans = _detect_types(SENTENCE, _SEVERAL_TYPES)

    assert [(SENTENCE[s.start : s.end], s.entity_type) for s in spans] == [(VALUE, expected_type)]


def test_the_entity_type_key_is_read_when_several_types_are_requested(ollama: _FakeOllama) -> None:
    ollama.reply = json.dumps([{"text": VALUE, "entity_type": "Religion"}])

    spans = _detect_types(SENTENCE, _SEVERAL_TYPES)

    assert [s.entity_type for s in spans] == ["RELIGION"]


@pytest.mark.parametrize("label", ["DISEASE", "SPORT", "", None])
def test_a_value_with_an_unrelated_or_missing_label_is_masked_under_the_first_requested_type(
    ollama: _FakeOllama, label: str | None, caplog: pytest.LogCaptureFixture
) -> None:
    """Another type's placeholder still hides the value and is restored locally; dropping it leaks it."""
    text = f"{SENTENCE} Takes Metformin daily."
    ollama.reply = json.dumps(
        [{"text": "Metformin", "type": "MEDICATION"}, {"text": VALUE, "type": label}]
    )

    with caplog.at_level("DEBUG"):
        spans = _detect_types(text, ["RELIGION", "MEDICATION"])

    assert [(text[s.start : s.end], s.entity_type) for s in spans] == [
        (VALUE, "RELIGION"),
        ("Metformin", "MEDICATION"),
    ]
    assert "could not place" not in caplog.text


@pytest.mark.parametrize("item", [{"text": "metformin", "type": "DRUG"}, "metformin"])
def test_a_reply_whose_only_item_has_an_unmapped_label_is_masked_not_refused(
    ollama: _FakeOllama, item: Any
) -> None:
    text = "Takes metformin daily."
    ollama.reply = json.dumps([item])

    spans = _detect_types(text, ["DIAGNOSIS", "MEDICATION"])

    assert [(text[s.start : s.end], s.entity_type) for s in spans] == [("metformin", "DIAGNOSIS")]


def test_a_value_guessed_onto_an_id_like_type_is_masked_even_without_digits(
    ollama: _FakeOllama,
) -> None:
    """The digit check rejects ID-like values the model labelled so; a guessed type never drops one."""
    text = "Patient has asthma."
    ollama.reply = json.dumps([{"text": "asthma", "type": "SPORT"}])

    spans = _detect_types(text, ["BIOMETRIC_ID", "DIAGNOSIS"])

    assert [(text[s.start : s.end], s.entity_type) for s in spans] == [("asthma", "BIOMETRIC_ID")]


@pytest.mark.parametrize("label", ["SPORT", None])
def test_with_one_id_like_type_requested_a_value_whose_label_names_another_is_masked(
    ollama: _FakeOllama, label: str | None
) -> None:
    """The digit check applies only to a type the label named, even with one type requested."""
    text = "Patient has asthma."
    ollama.reply = json.dumps([{"text": "asthma", "type": label}])

    spans = _detect_types(text, ["BIOMETRIC_ID"])

    assert [(text[s.start : s.end], s.entity_type) for s in spans] == [("asthma", "BIOMETRIC_ID")]


def test_with_one_id_like_type_requested_a_digitless_value_labelled_so_is_dropped(
    ollama: _FakeOllama,
) -> None:
    text = "Patient has asthma."
    ollama.reply = json.dumps([{"text": "asthma", "type": "BIOMETRIC_ID"}])

    assert _detect_types(text, ["BIOMETRIC_ID"]) == []


def _first_slice_only_reply() -> Callable[[str], str]:
    """A model that reports ``VALUE`` for the first prompt it gets and nothing for the rest."""
    calls: list[str] = []

    def _reply(prompt: str) -> str:
        calls.append(prompt)
        return FOUND if len(calls) == 1 else "[]"

    return _reply


def _text_with_value_after(words: int) -> tuple[str, int]:
    return "lorem " * words + VALUE + " " + "lorem " * 400, words * 6


def test_a_value_inside_the_overlap_window_after_the_first_slice_is_placed(
    ollama: _FakeOllama,
) -> None:
    text, start = _text_with_value_after(334)
    first_end = len(contextual_slices(text)[0][1])
    assert first_end <= start
    assert start + len(VALUE) <= first_end + 200
    ollama.reply = _first_slice_only_reply()

    spans = _detect(text)

    assert [(s.start, s.end) for s in spans] == [(start, start + len(VALUE))]


def test_a_value_that_exists_only_beyond_the_slice_and_its_overlap_window_is_not_placed(
    ollama: _FakeOllama,
) -> None:
    from septum_api.errors import SemanticLayerUnavailableError

    text, start = _text_with_value_after(380)
    first_end = len(contextual_slices(text)[0][1])
    assert start >= first_end + 200
    ollama.reply = _first_slice_only_reply()

    with pytest.raises(SemanticLayerUnavailableError) as raised:
        _detect(text)

    assert "unplaceable_reply" in str(raised.value)


def test_a_value_cut_by_the_overlap_window_is_unplaced_and_its_only_reply_refused(
    ollama: _FakeOllama,
) -> None:
    """Only "Type" lies inside the window; "diabetes" would leak, so the value is not placed."""
    from septum_api.errors import SemanticLayerUnavailableError

    text, start = _text_with_value_after(364)
    first_end = len(contextual_slices(text)[0][1])
    assert start < first_end + 200 < start + len(VALUE)
    ollama.reply = _first_slice_only_reply()

    with pytest.raises(SemanticLayerUnavailableError) as raised:
        _detect(text)

    assert "unplaceable_reply" in str(raised.value)


@pytest.mark.parametrize("unit", ["\U0001f600", "\U0001f600\U0001f525 ", "\U00020000a "])
def test_slices_of_four_byte_text_stay_within_the_byte_budget_and_overlap(unit: str) -> None:
    from septum_api.services.sanitizer import _contextual_slice_budget

    budget = _contextual_slice_budget(["DIAGNOSIS"], ollama_client.OLLAMA_NUM_CTX)
    text = unit * (12_000 // len(unit))

    slices = contextual_slices(text, budget)

    assert len(slices) > 1
    assert all(len(piece.encode("utf-8")) <= budget for _, piece in slices)
    assert all(len(piece) < SLICE_LIMIT for _, piece in slices)
    assert slices[0][0] == 0
    assert slices[-1][0] + len(slices[-1][1]) == len(text)
    for (offset, piece), (next_offset, _) in zip(slices, slices[1:]):
        assert offset < next_offset <= offset + len(piece)


_SLICING_SECONDS = 5.0


def _bounded_slices(text: str, budget: int) -> list[tuple[int, str]]:
    """Slice ``text``, failing the test instead of hanging when slicing does not terminate."""

    def _stalled(signum: int, frame: Any) -> None:
        raise TimeoutError("slicing did not terminate")

    previous = signal.signal(signal.SIGALRM, _stalled)
    signal.setitimer(signal.ITIMER_REAL, _SLICING_SECONDS)
    try:
        return contextual_slices(text, budget)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _assert_slices_progress_and_overlap(text: str, budget: int) -> None:
    slices = _bounded_slices(text, budget)

    assert slices[0][0] == 0
    assert slices[-1][0] + len(slices[-1][1]) == len(text)
    assert all(text[offset : offset + len(piece)] == piece for offset, piece in slices)
    assert all(len(piece.encode("utf-8")) <= budget for _, piece in slices)
    for (offset, piece), (next_offset, _) in zip(slices, slices[1:]):
        assert offset < next_offset
        assert offset + len(piece) - next_offset >= 200


def _dense_text_with_space_at(unit: str, position: int) -> str:
    return unit * position + " " + unit * 3000


@pytest.mark.parametrize("unit", ["\U0001f600", "糖", "é"])
@pytest.mark.parametrize("position", [200, 201, 220, 400, 720])
def test_slicing_at_the_minimum_budget_progresses_when_the_only_space_sits_near_the_overlap(
    unit: str, position: int
) -> None:
    _assert_slices_progress_and_overlap(_dense_text_with_space_at(unit, position), MIN_SLICE_BYTES)


def test_slicing_dense_text_progresses_at_every_budget_from_the_minimum_to_2000() -> None:
    texts = [
        _dense_text_with_space_at(unit, position)
        for unit in ("\U0001f600", "糖", "é")
        for position in (200, 220, 300, 450, 720)
    ]
    texts.append(("\U0001f600" * 199 + " ") * 30)
    texts.append(("糖" * 230 + " ") * 30)

    for budget in range(MIN_SLICE_BYTES, MIN_SLICE_BYTES + 401):
        for text in texts:
            _assert_slices_progress_and_overlap(text, budget)


def test_slicing_refuses_a_budget_below_the_minimum() -> None:
    with pytest.raises(ValueError):
        contextual_slices("a" * 5000, MIN_SLICE_BYTES - 1)


def test_a_value_after_many_four_byte_characters_gets_its_absolute_character_offset(
    ollama: _FakeOllama,
) -> None:
    ollama.reply = _reply_with_whole_values_of
    text = "\U0001f600 " * 5000 + SENTENCE + " \U0001f525" * 100

    spans = _detect(text)

    expected = text.index(VALUE)
    assert [(s.start, s.end) for s in spans] == [(expected, expected + len(VALUE))]


def test_the_window_boundary_is_exact_at_the_request_level(
    ollama: _FakeOllama, caplog: pytest.LogCaptureFixture
) -> None:
    ceiling = ollama_client.OLLAMA_NUM_CTX - ollama_client.OLLAMA_NUM_PREDICT
    marker = "private-prompt-marker"
    refused: list[str] = []

    for count in (ceiling - 1, ceiling, ceiling + 1):
        ollama.extra = {"prompt_eval_count": count}
        try:
            with caplog.at_level("DEBUG"):
                ollama_client.request_ollama_sync(marker, allow_truncated=False)
        except ollama_client.OllamaCallError as exc:
            refused.append(str(exc))

    assert refused == ["context_overflow", "context_overflow"]
    assert marker not in caplog.text
    ollama.extra = {"prompt_eval_count": ceiling}
    assert ollama_client.request_ollama_sync(marker, allow_truncated=True) == FOUND


# ---------------------------------------------------------------- the model's real window


def test_a_model_trained_on_a_short_context_gets_slices_that_fit_its_window(
    ollama: _FakeOllama,
) -> None:
    """Ollama lowers num_ctx to the trained length and cuts the prompt; slices must fit that."""
    ollama.trained_context = SHORT_CONTEXT
    ollama.reply = "[]"
    text = "患者患有糖尿病需要治疗" * 800

    _detect(text)

    worst = max(len(p.encode("utf-8")) for p in ollama.prompts)
    assert worst + ollama_client.OLLAMA_NUM_PREDICT < SHORT_CONTEXT
    assert _sent_every_slice(ollama, text, ["DIAGNOSIS"], SHORT_CONTEXT)
    assert {p["options"]["num_ctx"] for p in ollama.payloads} == {SHORT_CONTEXT}


def test_a_model_window_too_small_for_a_slice_refuses_before_any_prompt_is_sent(
    ollama: _FakeOllama,
) -> None:
    """After the answer budget and the prompt, a 4096-token window leaves no room for a slice."""
    from septum_api.errors import SemanticLayerUnavailableError

    ollama.trained_context = TOO_SHORT_CONTEXT

    with pytest.raises(SemanticLayerUnavailableError) as raised:
        _detect(SENTENCE)

    assert "prompt_too_long" in str(raised.value)
    assert ollama.prompts == []


def test_the_trained_length_is_read_once_per_server_and_model(ollama: _FakeOllama) -> None:
    _detect(SENTENCE)
    _detect(SENTENCE)

    assert ollama.show_requests == [
        ("http://localhost:11500/api/show", {"model": "ctx-model:1b"})
    ]


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """A settable monotonic clock for the window cache."""
    now = [1000.0]
    monkeypatch.setattr(ollama_client, "time", SimpleNamespace(monotonic=lambda: now[0]))
    return now


async def test_the_trained_length_is_read_again_once_its_cache_entry_expires(
    ollama: _FakeOllama, clock: list[float]
) -> None:
    """A model tag swapped on the server is picked up without a restart."""
    _detect(SENTENCE)
    clock[0] += ollama_client._WINDOW_TTL_SECONDS - 1
    _detect(SENTENCE)
    assert len(ollama.show_requests) == 1

    ollama.trained_context = SHORT_CONTEXT
    clock[0] += 2
    await clear_detection_cache()
    _detect(SENTENCE)

    assert len(ollama.show_requests) == 2
    assert ollama.payloads[-1]["options"]["num_ctx"] == SHORT_CONTEXT


async def test_a_failed_lookup_is_not_retried_by_lenient_calls_until_it_expires(
    ollama: _FakeOllama, clock: list[float]
) -> None:
    ollama.show_status = 500
    ollama_client.call_ollama_sync("p", base_url="http://localhost:11500")
    await ollama_client.call_ollama_async("p", base_url="http://localhost:11500")
    ollama_client.call_ollama_sync("p", base_url="http://localhost:11500")
    assert len(ollama.show_requests) == 1
    assert all("num_ctx" not in p["options"] for p in ollama.payloads)

    ollama.show_status = 200
    clock[0] += ollama_client._FAILED_LOOKUP_TTL_SECONDS + 1
    ollama_client.call_ollama_sync("p", base_url="http://localhost:11500")

    assert len(ollama.show_requests) == 2
    assert ollama.payloads[-1]["options"]["num_ctx"] == ollama_client.OLLAMA_NUM_CTX


def test_a_strict_call_reads_the_length_again_after_a_lenient_failure(
    ollama: _FakeOllama, clock: list[float]
) -> None:
    """A cached failure never refuses detection once the server answers again."""
    ollama.show_status = 500
    ollama_client.call_ollama_sync("p", base_url="http://localhost:11500", model="ctx-model:1b")
    ollama.show_status = 200

    assert _masked_texts(SENTENCE, _detect(SENTENCE)) == [VALUE]
    assert len(ollama.show_requests) == 2


def test_a_prompt_over_a_short_trained_window_is_refused_as_context_overflow(
    ollama: _FakeOllama,
) -> None:
    from septum_api.errors import SemanticLayerUnavailableError

    ollama.trained_context = SHORT_CONTEXT
    ollama.extra = {"prompt_eval_count": SHORT_CONTEXT - ollama_client.OLLAMA_NUM_PREDICT}

    with pytest.raises(SemanticLayerUnavailableError) as raised:
        _detect(SENTENCE)

    assert "context_overflow" in str(raised.value)


@pytest.mark.parametrize(
    "arrange",
    [
        lambda f: setattr(f, "show_status", 404),
        lambda f: setattr(f, "show_body", {"license": "x"}),
        lambda f: setattr(f, "show_body", {"model_info": {"llama.context_length": 4096}}),
        lambda f: setattr(f, "show_body", {"model_info": {"general.architecture": "llama"}}),
        lambda f: setattr(
            f,
            "show_body",
            {"model_info": {"general.architecture": "llama", "llama.context_length": "4096"}},
        ),
        lambda f: setattr(
            f,
            "show_body",
            {"model_info": {"general.architecture": "llama", "llama.context_length": 0}},
        ),
    ],
    ids=["http-error", "no-model-info", "no-architecture", "no-length", "length-not-int", "zero"],
)
def test_an_unreadable_trained_length_refuses_before_any_prompt_is_sent(
    ollama: _FakeOllama, arrange: Callable[[_FakeOllama], Any]
) -> None:
    from septum_api.errors import SemanticLayerUnavailableError

    arrange(ollama)

    with pytest.raises(SemanticLayerUnavailableError) as raised:
        _detect(SENTENCE)

    assert "unknown_context_length" in str(raised.value)
    assert ollama.prompts == []


def test_an_unreadable_trained_length_is_not_cached(ollama: _FakeOllama) -> None:
    from septum_api.errors import SemanticLayerUnavailableError

    ollama.show_status = 500
    with pytest.raises(SemanticLayerUnavailableError):
        _detect(SENTENCE)
    ollama.show_status = 200

    assert _masked_texts(SENTENCE, _detect(SENTENCE)) == [VALUE]


def test_a_lenient_sync_call_with_an_unreadable_window_leaves_num_ctx_out(
    ollama: _FakeOllama,
) -> None:
    """A guessed 8192 would differ from the next call's window and reload the model."""
    ollama.show_status = 500

    assert ollama_client.call_ollama_sync("p", base_url="http://localhost:11500") == FOUND
    assert "num_ctx" not in ollama.payloads[0]["options"]
    assert ollama.show_requests


def test_a_refused_url_never_reaches_the_metadata_endpoint(ollama: _FakeOllama) -> None:
    from septum_api.errors import SemanticLayerUnavailableError

    with pytest.raises(SemanticLayerUnavailableError):
        _detect(SENTENCE, _settings(ollama_base_url="http://203.0.113.5:11434"))

    assert ollama.show_requests == []


# ---------------------------------------------------------------- one num_ctx for every call


async def test_an_async_call_sends_the_same_num_ctx_as_a_sync_call(ollama: _FakeOllama) -> None:
    ollama.trained_context = SHORT_CONTEXT

    await ollama_client.call_ollama_async("p", base_url="http://localhost:11500", model="m")
    ollama_client.call_ollama_sync("p", base_url="http://localhost:11500", model="m")

    assert [p["options"]["num_ctx"] for p in ollama.payloads] == [SHORT_CONTEXT, SHORT_CONTEXT]


async def test_an_async_call_with_caller_options_keeps_them_and_adds_num_ctx(
    ollama: _FakeOllama,
) -> None:
    await ollama_client.call_ollama_async(
        "p", base_url="http://localhost:11500", options={"num_predict": 5, "temperature": 0.0}
    )

    assert ollama.payloads[0]["options"] == {
        "num_predict": 5,
        "temperature": 0.0,
        "num_ctx": ollama_client.OLLAMA_NUM_CTX,
    }


async def test_the_intent_classifier_request_carries_num_ctx(ollama: _FakeOllama) -> None:
    ollama.reply = "CHAT"

    intent = await chat_module._classify_query_intent(
        "hello", ["a.pdf"], "http://localhost:11500", "ctx-model:1b", "ctx-model:1b"
    )

    assert intent == "chat"
    options = ollama.payloads[0]["options"]
    assert options["num_ctx"] == ollama_client.OLLAMA_NUM_CTX
    assert options["num_predict"] == 5


async def test_a_full_context_call_asks_for_the_whole_trained_length(ollama: _FakeOllama) -> None:
    await ollama_client.call_ollama_async(
        "p", base_url="http://localhost:11500", model="chat:8b", full_context=True
    )

    assert ollama.payloads[0]["options"]["num_ctx"] == TRAINED_CONTEXT


@pytest.mark.parametrize(
    "chat_model, expected",
    [("chat:8b", TRAINED_CONTEXT), ("ctx-model:1b", ollama_client.OLLAMA_NUM_CTX)],
    ids=["other-model", "detection-model"],
)
async def test_the_intent_classifier_caps_num_ctx_only_for_the_detection_model(
    ollama: _FakeOllama, chat_model: str, expected: int
) -> None:
    ollama.reply = "CHAT"

    await chat_module._classify_query_intent(
        "hello", ["a.pdf"], "http://localhost:11500", chat_model, "ctx-model:1b"
    )

    assert ollama.payloads[0]["options"]["num_ctx"] == expected


@pytest.mark.parametrize(
    "chat_model, expected",
    [("chat:8b", TRAINED_CONTEXT), ("ctx-model:1b", ollama_client.OLLAMA_NUM_CTX)],
    ids=["other-model", "detection-model"],
)
async def test_the_local_chat_provider_caps_num_ctx_only_for_the_detection_model(
    ollama: _FakeOllama, chat_model: str, expected: int
) -> None:
    from septum_api.services.llm_providers.base import LLMProviderConfig
    from septum_api.services.llm_providers.ollama import OllamaProvider

    provider = OllamaProvider(LLMProviderConfig(_settings(ollama_chat_model=chat_model)))

    await provider.complete([{"role": "user", "content": "long context"}], 0.0, None)

    assert ollama.payloads[0]["options"]["num_ctx"] == expected


@pytest.mark.parametrize(
    "chat_model, expected",
    [("chat:8b", TRAINED_CONTEXT), ("ctx-model:1b", ollama_client.OLLAMA_NUM_CTX)],
    ids=["other-model", "detection-model"],
)
async def test_the_router_fallback_caps_num_ctx_only_for_the_detection_model(
    ollama: _FakeOllama, chat_model: str, expected: int
) -> None:
    router = LLMRouter(_settings(ollama_chat_model=chat_model))

    await router._fallback_via_ollama([{"role": "user", "content": "long context"}])

    assert ollama.payloads[0]["options"]["num_ctx"] == expected


async def test_a_chat_call_with_an_unreadable_window_leaves_num_ctx_out(
    ollama: _FakeOllama,
) -> None:
    ollama.show_status = 500
    ollama.reply = "CHAT"

    await chat_module._classify_query_intent(
        "hello", ["a.pdf"], "http://localhost:11500", "chat:8b", "ctx-model:1b"
    )

    assert "num_ctx" not in ollama.payloads[0]["options"]


async def test_an_unreadable_window_never_blocks_an_async_call(ollama: _FakeOllama) -> None:
    ollama.show_status = 500

    out = await ollama_client.call_ollama_async("p", base_url="http://localhost:11500")

    assert out == FOUND
    assert "num_ctx" not in ollama.payloads[0]["options"]
    assert ollama.show_requests


# ---------------------------------------------------------------- placement robustness


@pytest.mark.parametrize(
    "reported, text, expected",
    [
        ("Crohn’s disease", "Diagnosed with Crohn's disease in May.", ["Crohn's disease"]),
        ("Crohn's disease", "Diagnosed with Crohn’s disease in May.", ["Crohn’s disease"]),
        ("Crohnʼs disease", "Diagnosed with Crohn's disease in May.", ["Crohn's disease"]),
        ("type-2 diabetes", "The patient has type 2 diabetes.", ["type 2 diabetes"]),
        ("type 2 diabetes", "The patient has type-2 diabetes.", ["type-2 diabetes"]),
        ("type 2 diabetes", "The patient has type–2 diabetes.", ["type–2 diabetes"]),
        ("seker hastasi", "Hasta şeker hastası olarak izleniyor.", ["şeker hastası"]),
        ("ΔΙΑΒΗΤΗΣ", "Ο ασθενής έχει Διαβήτης τύπου 2.", ["Διαβήτης"]),
        ("type 2 diabetes (T2DM)", "Has type 2 diabetes T2DM today.", ["type 2 diabetes T2DM"]),
        ("“Sugar” disease", 'Calls it "sugar" disease.', ['sugar" disease']),
    ],
    ids=[
        "curly-apostrophe-reported", "curly-apostrophe-in-text", "modifier-apostrophe",
        "dash-reported", "dash-in-text", "en-dash-in-text", "diacritics-dropped",
        "accentless-caps", "punctuation-dropped", "quote-variants",
    ],
)
def test_a_reported_value_is_placed_despite_punctuation_dashes_and_diacritics(
    ollama: _FakeOllama, reported: str, text: str, expected: list[str]
) -> None:
    ollama.reply = _reply_with(reported)

    assert _masked_texts(text, _detect(text)) == expected


@pytest.mark.parametrize(
    "reported, text, expected",
    [
        ("Diabète", "Diabète noted; diabete misspelled later.", ["Diabète", "diabete"]),
        (
            "şeker hastası",
            "Hasta şeker hastası. Sonra SEKER HASTASI.",
            ["şeker hastası", "SEKER HASTASI"],
        ),
    ],
    ids=["accent-dropped-repeat", "caps-repeat-without-marks"],
)
def test_a_repeat_written_without_the_marks_is_masked_beside_the_exact_hit(
    ollama: _FakeOllama, reported: str, text: str, expected: list[str]
) -> None:
    ollama.reply = _reply_with(reported)

    assert _masked_texts(text, _detect(text)) == expected


@pytest.mark.parametrize(
    "reported, text, expected",
    [
        ("dai thao duong", "Benh nhan bi đái tháo đường type 2.", ["đái tháo đường"]),
        ("zoltaczka mechaniczna", "Rozpoznanie: żółtaczka mechaniczna.", ["żółtaczka mechaniczna"]),
        ("Sorensen syndrome", "Has Sørensen syndrome since May.", ["Sørensen syndrome"]),
        ("ĐÁI THÁO ĐƯỜNG", "Benh nhan bi dai thao duong.", ["dai thao duong"]),
    ],
    ids=["d-with-stroke", "l-with-stroke", "o-with-stroke", "stroke-in-the-reported-value"],
)
def test_a_value_is_placed_whole_across_letters_with_a_stroke(
    ollama: _FakeOllama, reported: str, text: str, expected: list[str]
) -> None:
    ollama.reply = _reply_with(reported)

    assert _masked_texts(text, _detect(text)) == expected


def test_offsets_stay_exact_after_many_stroke_letters(ollama: _FakeOllama) -> None:
    text = "đĐłŁøØħĦŧŦ " * 50 + "đái tháo đường var."
    ollama.reply = _reply_with("dai thao duong")

    assert _masked_texts(text, _detect(text)) == ["đái tháo đường"]


def test_offsets_stay_exact_after_many_decomposable_characters(ollama: _FakeOllama) -> None:
    text = "ğüşıöç İĞÜŞ " * 50 + "şeker hastası var."
    ollama.reply = _reply_with("seker hastasi")

    assert _masked_texts(text, _detect(text)) == ["şeker hastası"]


def test_the_found_words_of_a_partly_found_value_are_masked_and_the_value_counted_unplaced(
    ollama: _FakeOllama, caplog: pytest.LogCaptureFixture
) -> None:
    """Every word of three or more letters the phrase shares with the text is masked; "of" is not."""
    text = "Chronic renal failure of stage 3. Type 2 diabetes."
    ollama.reply = json.dumps(
        [
            {"text": "chronic kidney failure of stage", "type": "DIAGNOSIS"},
            {"text": VALUE, "type": "DIAGNOSIS"},
        ]
    )

    with caplog.at_level("DEBUG"):
        spans = _detect(text)

    assert _masked_texts(text, spans) == ["Chronic", "failure", "stage", VALUE]
    assert "1 of 2" in caplog.text
    assert "kidney" not in caplog.text


@pytest.mark.parametrize(
    "reported, text",
    [
        ("chronic kidney failure of stage", "Chronic renal failure of stage 3."),
        ("Mao Li", "Patient Li, Mao was admitted."),
    ],
    ids=["missing-word", "short-word-left"],
)
def test_a_reply_whose_only_value_is_partly_found_is_refused(
    ollama: _FakeOllama, reported: str, text: str
) -> None:
    from septum_api.errors import SemanticLayerUnavailableError

    ollama.reply = _reply_with(reported)

    with pytest.raises(SemanticLayerUnavailableError) as raised:
        _detect(text)

    assert "unplaceable_reply" in str(raised.value)


def test_a_token_buried_in_a_compound_is_not_placed(ollama: _FakeOllama) -> None:
    """The token rule keeps word boundaries: "Krebs" inside "Lungenkrebs" is no occurrence."""
    from septum_api.errors import SemanticLayerUnavailableError

    ollama.reply = _reply_with("Krebs")

    with pytest.raises(SemanticLayerUnavailableError) as raised:
        _detect("Diagnose: Lungenkrebs im Stadium 2.")

    assert "unplaceable_reply" in str(raised.value)


# ---------------------------------------------------------------- short values


@pytest.mark.parametrize(
    "value, text, expected",
    [
        ("a", "Type a. Patient has asthma.", ["a"]),
        ("IV", "IV drip given; IVF planned.", ["IV"]),
        ("T2", "T2 lesion, T2DM noted.", ["T2"]),
    ],
)
def test_a_value_shorter_than_three_letters_is_never_widened_into_a_longer_word(
    ollama: _FakeOllama, value: str, text: str, expected: list[str]
) -> None:
    ollama.reply = _reply_with(value)

    assert _masked_texts(text, _detect(text)) == expected


# ---------------------------------------------------------------- slice overlap


@pytest.mark.parametrize("word", ["a", "ab", "abc", "abcd", "abcde", "abcdefg"])
def test_consecutive_slices_of_spaced_text_overlap_by_at_least_the_overlap(word: str) -> None:
    ranges = _slice_ranges(f"{word} " * 5000)

    assert min(prev[1] - nxt[0] for prev, nxt in zip(ranges, ranges[1:])) >= 200


# ---------------------------------------------------------------- candidate validation


_VALIDATION_TEXT = "Alice met Bob in London."


def _validation_candidates() -> list[Any]:
    from septum_core.spans import DetectedSpan

    return [
        DetectedSpan(start=0, end=5, entity_type="PERSON_NAME", score=0.9),
        DetectedSpan(start=10, end=13, entity_type="PERSON_NAME", score=0.9),
        DetectedSpan(start=17, end=23, entity_type="LOCATION", score=0.8),
    ]


def _echo_first_candidate() -> str:
    return json.dumps(
        [{"text": "Alice", "entity_type": "PERSON_NAME", "start": 0, "end": 5}]
    )


def _validate(settings: AppSettings | None = None) -> list[Any]:
    return _adapter(settings or _settings(use_ollama_validation_layer=True)).validate_candidates(
        text=_VALIDATION_TEXT,
        candidate_spans=_validation_candidates(),
        language="en",
        regulation_context="rules",
    )


@pytest.mark.parametrize(
    "extra",
    [
        {"done_reason": "length", "prompt_eval_count": MEASURED_PROMPT_TOKENS},
        {"prompt_eval_count": ollama_client.OLLAMA_NUM_CTX},
        {},
    ],
    ids=["reply-cut", "prompt-cut", "unmeasured"],
)
def test_validation_keeps_every_candidate_when_the_prompt_or_reply_may_be_cut(
    ollama: _FakeOllama, extra: dict[str, Any]
) -> None:
    ollama.reply = _echo_first_candidate()
    ollama.extra = extra

    assert _validate() == _validation_candidates()
    assert ollama.payloads[0]["options"]["num_ctx"] == ollama_client.OLLAMA_NUM_CTX


def test_validation_keeps_every_candidate_when_the_window_is_unreadable(
    ollama: _FakeOllama,
) -> None:
    ollama.reply = _echo_first_candidate()
    ollama.show_status = 500

    assert _validate() == _validation_candidates()
    assert ollama.prompts == []


def test_validation_of_a_whole_answer_keeps_only_the_confirmed_candidates(
    ollama: _FakeOllama,
) -> None:
    ollama.reply = _echo_first_candidate()

    assert _validate() == _validation_candidates()[:1]
