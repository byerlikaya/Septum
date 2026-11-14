"""llm_prompt custom rules: policy digest, fail-closed Ollama calls, and the stored Ollama URL/model.

Ollama is never reached: the HTTP client factory of the Ollama client is replaced.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from septum_api import main as main_module
from septum_api.database import build_default_app_settings, get_db
from septum_api.errors import (
    CUSTOM_RULE_UNAVAILABLE_CODE,
    CUSTOM_RULE_UNAVAILABLE_DETAIL,
    CustomRuleUnavailableError,
)
from septum_api.models import Base
from septum_api.models.document import Chunk, Document
from septum_api.models.regulation import CustomRecognizer
from septum_api.models.settings import AppSettings
from septum_api.models.user import User
from septum_api.routers import chat as chat_module
from septum_api.routers import documents as documents_module
from septum_api.services import document_anon_store, ollama_client, pii_gate
from septum_api.services.anonymization_map import AnonymizationMap
from septum_api.services.ingestion.base import IngestionResult
from septum_api.services.llm_router import LLMRouter
from septum_api.services.pii_gate import gate_untrusted_texts
from septum_api.services.policy_composer import PolicyComposer
from septum_api.services.policy_snapshot import (
    build_policy_snapshot,
    current_policy_snapshot,
    detection_settings,
)
from septum_api.services.recognizers.registry import (
    LLMContextConfig,
    LLMContextRecognizer,
)
from septum_api.services.remask_cache import remask_cache
from septum_api.services.sanitizer import PIISanitizer
from septum_api.services.sanitizer_factory import create_sanitizer
from septum_api.utils.auth_dependency import get_current_user
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

SECRET = "Bluefinch"
TEXT = f"The launch of {SECRET} moves to next quarter."
FOUND = f'[{{"text": "{SECRET}"}}]'
MEASURED_PROMPT_TOKENS = 600


class _Response:
    def __init__(self, status: int, body: Any) -> None:
        self._status = status
        self._body = body

    def raise_for_status(self) -> None:
        if self._status >= 400:
            request = httpx.Request("POST", "http://localhost/api/generate")
            raise httpx.HTTPStatusError(
                "status", request=request, response=httpx.Response(self._status, request=request)
            )

    def json(self) -> Any:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class _FakeOllama:
    """Stands in for the Ollama HTTP client; records each request."""

    def __init__(self, reply: str = FOUND) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.error: Exception | None = None
        self.status = 200
        self.body: Any = {"response": reply}
        self.trained_context = 131072
        self.show_status = 200
        self.show_calls: list[tuple[str, dict]] = []

    def __call__(self, timeout: Any) -> "_FakeOllama":
        return self

    def __enter__(self) -> "_FakeOllama":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def post(self, url: str, json: dict) -> _Response:
        if url.endswith("/api/show"):
            self.show_calls.append((url, json))
            info = {"general.architecture": "llama", "llama.context_length": self.trained_context}
            return _Response(self.show_status, {"model_info": info})
        self.calls.append((url, json))
        if self.error is not None:
            raise self.error
        if isinstance(self.body, dict):
            return _Response(self.status, {"prompt_eval_count": MEASURED_PROMPT_TOKENS, **self.body})
        return _Response(self.status, self.body)


@pytest.fixture
def ollama(monkeypatch: pytest.MonkeyPatch) -> _FakeOllama:
    fake = _FakeOllama()
    monkeypatch.setattr(ollama_client, "ollama_http_client", fake)
    return fake


def _settings(**overrides: Any) -> AppSettings:
    fields: dict[str, Any] = dict(
        id=1,
        use_presidio_layer=True,
        use_ner_layer=False,
        use_ollama_validation_layer=False,
        use_ollama_semantic_layer=False,
        use_ollama_layer=False,
        ollama_base_url="http://localhost:11500",
        ollama_deanon_model="rule-model:1b",
    )
    fields.update(overrides)
    return AppSettings(**fields)


def _llm_rule(rule_id: int = 7) -> CustomRecognizer:
    return CustomRecognizer(
        id=rule_id,
        name="Project Codename",
        entity_type="PROJECT_CODENAME",
        detection_method="llm_prompt",
        pattern=None,
        keywords=None,
        llm_prompt="Find internal project codenames.",
        context_words=[],
        placeholder_label="PROJECT_CODENAME",
        is_active=True,
    )


def _regex_rule() -> CustomRecognizer:
    return CustomRecognizer(
        id=8,
        name="Patient File Number",
        entity_type="PATIENT_FILE_NUMBER",
        detection_method="regex",
        pattern=r"PFN-[0-9]{6}",
        keywords=None,
        llm_prompt=None,
        context_words=["file"],
        placeholder_label="PATIENT_FILE_NUMBER",
        is_active=True,
    )


def _pre_fix_definition(rule: CustomRecognizer) -> tuple:
    return (
        rule.entity_type,
        rule.detection_method,
        rule.pattern,
        rule.keywords,
        rule.llm_prompt,
        rule.context_words,
    )


def _recognizer(settings: AppSettings | None = None) -> LLMContextRecognizer:
    settings = settings or _settings()
    return LLMContextRecognizer(
        LLMContextConfig(
            name="Project Codename",
            entity_type="PROJECT_CODENAME",
            llm_prompt="Find internal project codenames.",
            context_words=[],
            rule_id=7,
            base_url=settings.ollama_base_url,
            model=settings.ollama_deanon_model,
        )
    )


async def _store(env: Any, *rows: Any) -> None:
    async with env.session_maker() as session:
        session.add_all(list(rows))
        await session.commit()


async def test_documents_masked_under_an_llm_prompt_rule_become_outdated(env) -> None:
    """A digest recorded while llm_prompt rules masked nothing no longer matches the current one."""
    rule = _llm_rule()
    await _store(env, rule)
    defaults = build_default_app_settings()
    pre_fix = build_policy_snapshot(
        [], [_pre_fix_definition(rule)], [], detection=detection_settings(defaults)
    )

    async with env.session_maker() as session:
        read = await current_policy_snapshot(session)
        _, composed = await PolicyComposer().compose_with_snapshot(session, defaults)

    assert read.digest == composed.digest
    assert read.digest != pre_fix.digest


async def test_policy_without_llm_prompt_rules_keeps_its_digest(env) -> None:
    """Documents masked under regex/keyword rules without context words stay current."""
    rule = _regex_rule()
    rule.context_words = []
    await _store(env, rule)
    defaults = build_default_app_settings()
    pre_fix = build_policy_snapshot(
        [], [_pre_fix_definition(rule)], [], detection=detection_settings(defaults)
    )

    async with env.session_maker() as session:
        read = await current_policy_snapshot(session)
        _, composed = await PolicyComposer().compose_with_snapshot(session, defaults)

    assert read.digest == pre_fix.digest
    assert composed.digest == pre_fix.digest


def test_rule_model_counts_in_the_digest_only_with_an_llm_prompt_rule() -> None:
    """With every Ollama layer off, the model moves the digest only when an llm_prompt rule is active."""
    llm = [_pre_fix_definition(_llm_rule())]
    regex = [_pre_fix_definition(_regex_rule())]

    def digest(definitions: list, model: str, active: bool) -> str:
        detection = detection_settings(
            _settings(ollama_deanon_model=model), llm_prompt_active=active
        )
        return build_policy_snapshot([], definitions, [], detection=detection).digest

    assert digest(llm, "a:1b", True) != digest(llm, "b:1b", True)
    assert digest(regex, "a:1b", False) == digest(regex, "b:1b", False)


async def test_composed_rule_calls_the_stored_ollama_url_and_model(env, ollama) -> None:
    """Chat and ingestion compose with the settings row; the rule calls its URL and model."""
    await _store(env, _llm_rule())
    settings = _settings()
    async with env.session_maker() as session:
        policy, _ = await PolicyComposer().compose_with_snapshot(session, settings)
    anon_map = AnonymizationMap(document_id=1, language="en")

    result = PIISanitizer(settings=settings, policy=policy).sanitize(TEXT, "en", anon_map)

    assert [(url, body["model"]) for url, body in ollama.calls] == [
        ("http://localhost:11500/api/generate", "rule-model:1b")
    ]
    assert SECRET not in result.sanitized_text


async def test_sanitizer_factory_binds_the_rule_to_the_settings(env, ollama) -> None:
    """The factory behind chat queries and the PII gate passes its settings to the rule."""
    await _store(env, _llm_rule())
    async with env.session_maker() as session:
        sanitizer = await create_sanitizer(session, _settings(ollama_deanon_model="other:2b"))

    sanitizer.sanitize(TEXT, "en", AnonymizationMap(document_id=1, language="en"))

    assert [body["model"] for _, body in ollama.calls] == ["other:2b"]


def _connect_error() -> Exception:
    return httpx.ConnectError("refused")


def _timeout() -> Exception:
    return httpx.ReadTimeout("slow")


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        (lambda f: setattr(f, "error", _connect_error()), "ConnectError"),
        (lambda f: setattr(f, "error", _timeout()), "ReadTimeout"),
        (lambda f: setattr(f, "status", 404), "HTTPStatusError"),
        (lambda f: setattr(f, "body", ValueError("not json")), "invalid_body"),
        (lambda f: setattr(f, "body", {"error": "model not found"}), "invalid_body"),
        (lambda f: setattr(f, "body", {"response": "I cannot help with that."}), "unparsable_reply"),
    ],
    ids=["unreachable", "timeout", "404", "non-json-body", "no-response-field", "unparsable-reply"],
)
def test_a_failed_rule_call_refuses_instead_of_finding_nothing(
    ollama: _FakeOllama, caplog: pytest.LogCaptureFixture, setup: Any, reason: str
) -> None:
    setup(ollama)

    with caplog.at_level(logging.WARNING), pytest.raises(CustomRuleUnavailableError) as raised:
        _recognizer().analyze(TEXT)

    assert raised.value.code == CUSTOM_RULE_UNAVAILABLE_CODE
    assert reason in str(raised.value)
    assert SECRET not in str(raised.value)
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "7" in logged and "Project Codename" in logged
    assert SECRET not in logged


def test_a_refused_ollama_url_refuses_without_a_request(ollama: _FakeOllama) -> None:
    with pytest.raises(CustomRuleUnavailableError):
        _recognizer(_settings(ollama_base_url="http://203.0.113.5:11434")).analyze(TEXT)

    assert ollama.calls == []


ID_TEXT = "Alice has id 123-45-6789 here."
ID_SPAN = [(13, 24)]
REFUSED = None


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"response": "[]"}, []),
        ({"response": "```json\n[ ]\n```"}, []),
        ({"response": "  ```\n[]\n```  "}, []),
        ({"response": '["123-45-6789"]'}, ID_SPAN),
        ({"response": '[{"start": 13, "end": 24, "text": "123-45-6789"}]'}, ID_SPAN),
        ({"response": '{"start": 13, "end": 24, "text": "123-45-6789", "tags": []}'}, REFUSED),
        (
            {"response": '[{"start": 13, "end": 24, "text": "123-45-6789", "aliases": []}, {"sta'},
            REFUSED,
        ),
        ({"response": 'Part one: []. Part two: [{"text": "123-45-6789"}]'}, REFUSED),
        ({"response": "Nothing found: []"}, REFUSED),
        ({"response": '[{"tags": []}, 5]'}, REFUSED),
        ({"response": "[]", "done_reason": "length"}, REFUSED),
        ({"response": '[{"text": "123-45-6789"}]', "done_reason": "stop"}, ID_SPAN),
    ],
    ids=[
        "empty",
        "fenced-empty",
        "bare-fenced-empty",
        "string-item",
        "dict-item",
        "bare-object-with-empty-field",
        "truncated-array",
        "empty-then-found",
        "prose-with-empty-array",
        "no-usable-item",
        "cut-at-token-limit",
        "done",
    ],
)
def test_rule_reply_outcomes(ollama: _FakeOllama, body: dict, expected: Any) -> None:
    """Only an exactly empty array means "found nothing"; anything untrustworthy refuses."""
    ollama.body = body

    if expected is REFUSED:
        with pytest.raises(CustomRuleUnavailableError):
            _recognizer().analyze(ID_TEXT)
    else:
        assert [(r.start, r.end) for r in _recognizer().analyze(ID_TEXT)] == expected


@pytest.mark.parametrize(
    "prompt_eval_count",
    [
        lambda: ollama_client.OLLAMA_NUM_CTX - ollama_client.OLLAMA_NUM_PREDICT,
        lambda: None,
    ],
    ids=["overflow", "unmeasured"],
)
def test_a_rule_prompt_the_server_may_have_cut_is_refused(
    ollama: _FakeOllama, prompt_eval_count: Any
) -> None:
    ollama.body = {"response": "[]", "prompt_eval_count": prompt_eval_count()}

    with pytest.raises(CustomRuleUnavailableError):
        _recognizer().analyze(ID_TEXT)

    assert ollama.calls[0][1]["options"]["num_ctx"] == ollama_client.OLLAMA_NUM_CTX


@pytest.mark.parametrize(
    "trained_context, expected_window",
    [(131072, ollama_client.OLLAMA_NUM_CTX), (4096, 4096)],
    ids=["default-window", "short-trained-window"],
)
@pytest.mark.parametrize("margin", [0, 1], ids=["at-the-ceiling", "beyond-it"])
def test_a_rule_prompt_over_the_model_window_is_refused_as_context_overflow(
    ollama: _FakeOllama, trained_context: int, expected_window: int, margin: int
) -> None:
    ollama.trained_context = trained_context
    count = expected_window - ollama_client.OLLAMA_NUM_PREDICT + margin
    ollama.body = {"response": "[]", "prompt_eval_count": count}

    with pytest.raises(CustomRuleUnavailableError) as raised:
        _recognizer().analyze(ID_TEXT)

    assert "context_overflow" in str(raised.value)
    assert ollama.calls[0][1]["options"]["num_ctx"] == expected_window


def test_a_rule_prompt_just_inside_a_short_trained_window_is_answered(
    ollama: _FakeOllama,
) -> None:
    ollama.trained_context = 4096
    ollama.body = {"response": "[]", "prompt_eval_count": 4096 - ollama_client.OLLAMA_NUM_PREDICT - 1}

    assert _recognizer().analyze(ID_TEXT) == []


def test_a_rule_whose_model_window_cannot_be_read_is_refused_without_a_prompt(
    ollama: _FakeOllama,
) -> None:
    ollama.show_status = 404

    with pytest.raises(CustomRuleUnavailableError) as raised:
        _recognizer().analyze(ID_TEXT)

    assert "unknown_context_length" in str(raised.value)
    assert ollama.calls == []


def test_other_ollama_callers_keep_the_empty_reply_contract(ollama: _FakeOllama) -> None:
    """call_ollama_sync still answers "" on a failed call; only the rule path raises."""
    ollama.error = _connect_error()
    assert ollama_client.call_ollama_sync("p", base_url="http://localhost:11500") == ""
    ollama.error = None
    ollama.body = ValueError("not json")
    assert ollama_client.call_ollama_sync("p", base_url="http://localhost:11500") == ""
    ollama.body = {"response": "[partial", "done_reason": "length"}
    assert ollama_client.call_ollama_sync("p", base_url="http://localhost:11500") == "[partial"


async def test_chat_query_sanitizing_raises_when_the_rule_fails(env, ollama) -> None:
    await _store(env, _llm_rule())
    ollama.error = _connect_error()
    anon_map = AnonymizationMap(document_id=0, language="en")

    async with env.session_maker() as session:
        with pytest.raises(CustomRuleUnavailableError):
            await chat_module._sanitize_query(TEXT, "en", _settings(), anon_map, session)

    assert SECRET not in anon_map.entity_map


async def test_pii_gate_raises_when_the_rule_fails(env, ollama) -> None:
    await _store(env, _llm_rule())
    ollama.error = _connect_error()

    async with env.session_maker() as session:
        with pytest.raises(CustomRuleUnavailableError):
            await gate_untrusted_texts(
                [TEXT], AnonymizationMap(document_id=0, language="en"), session, _settings()
            )


async def test_chat_turn_failure_becomes_a_coded_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded = AsyncMock()
    monkeypatch.setattr(chat_module, "_record_backend_error", recorded)
    exc = CustomRuleUnavailableError("Custom detection rule 7 could not run (ConnectError).")

    event = await chat_module._turn_error_event(exc, "s1", object(), object())

    assert event == {
        "type": "error",
        "code": CUSTOM_RULE_UNAVAILABLE_CODE,
        "message": CUSTOM_RULE_UNAVAILABLE_DETAIL,
    }
    assert recorded.await_args.args[3] == 503


async def test_route_failure_becomes_a_coded_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_module, "engine_is_ready", lambda: False)
    request = SimpleNamespace(scope={}, headers={}, client=None, method="POST")
    exc = CustomRuleUnavailableError("Custom detection rule 7 could not run (ConnectError).")

    response = await main_module.local_model_unavailable_handler(request, exc)

    assert response.status_code == 503
    assert response.body == (
        b'{"detail":"' + CUSTOM_RULE_UNAVAILABLE_DETAIL.encode() + b'","code":"'
        + CUSTOM_RULE_UNAVAILABLE_CODE.encode() + b'"}'
    )


def test_the_error_resolves_to_the_local_model_handler() -> None:
    handlers = main_module.app.exception_handlers
    resolved = next(
        handlers[cls] for cls in CustomRuleUnavailableError.__mro__ if cls in handlers
    )
    assert resolved is main_module.local_model_unavailable_handler


def _stored_settings() -> AppSettings:
    """A complete settings row: Presidio only, approval off, the test Ollama URL and model."""
    row = build_default_app_settings()
    row.use_presidio_layer = True
    row.use_ner_layer = False
    row.use_ollama_validation_layer = False
    row.use_ollama_semantic_layer = False
    row.use_ollama_layer = False
    row.require_approval = False
    row.ollama_base_url = "http://localhost:11500"
    row.ollama_deanon_model = "rule-model:1b"
    return row


class _ChatEnv:
    def __init__(self) -> None:
        self.client: AsyncClient
        self.doc_id: int
        self.prompts: list[str] = []
        self.router_calls = 0


@pytest.fixture
async def chat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ollama: _FakeOllama):
    """Real route, real sanitizer and rule; only Ollama, retrieval and the cloud LLM are doubles.

    The stored document was masked before the rule existed (its digest is the
    rule-free one) and still holds the secret in clear in its chunk.
    """
    from septum_api.main import app

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rule.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    state = _ChatEnv()

    async def _override_get_db():
        async with session_maker() as session:
            yield session

    chunk_text = f"Notes: the {SECRET} milestone slipped."
    async with session_maker() as session:
        user = User(email="owner@example.com", hashed_password="x", role="editor")
        session.add_all([user, _stored_settings(), _llm_rule()])
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
        state.prompts.append(messages[-1]["content"])
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


async def _ask(chat: _ChatEnv, message: str) -> list[dict[str, Any]]:
    resp = await chat.client.post(
        "/api/chat/ask", json={"message": message, "document_ids": [chat.doc_id]}
    )
    assert resp.status_code == 200, resp.text
    assert SECRET not in resp.text
    return [
        json.loads(line[len("data: "):])
        for line in resp.text.splitlines()
        if line.startswith("data: ")
    ]


async def test_chat_turn_with_a_failing_rule_streams_the_coded_error_and_calls_no_cloud_llm(
    chat: _ChatEnv, ollama: _FakeOllama
) -> None:
    ollama.error = _connect_error()

    events = await _ask(chat, f"When does {SECRET} ship?")

    errors = [e for e in events if e["type"] == "error"]
    assert errors == [
        {
            "type": "error",
            "code": CUSTOM_RULE_UNAVAILABLE_CODE,
            "message": CUSTOM_RULE_UNAVAILABLE_DETAIL,
        }
    ]
    assert not [e for e in events if e["type"] == "answer_chunk"]
    assert chat.router_calls == 0
    assert ollama.calls


@pytest.mark.parametrize(
    "prompt_eval_count",
    [ollama_client.OLLAMA_NUM_CTX - ollama_client.OLLAMA_NUM_PREDICT, ollama_client.OLLAMA_NUM_CTX],
    ids=["at-the-limit", "beyond-the-window"],
)
async def test_chat_turn_with_a_long_text_the_server_cut_is_refused_with_the_code_and_no_cloud_call(
    chat: _ChatEnv, ollama: _FakeOllama, prompt_eval_count: int
) -> None:
    ollama.body = {"response": "[]", "prompt_eval_count": prompt_eval_count}
    long_message = f"When does {SECRET} ship? " + "Please answer in detail. " * 600

    events = await _ask(chat, long_message)

    errors = [e for e in events if e["type"] == "error"]
    assert errors == [
        {
            "type": "error",
            "code": CUSTOM_RULE_UNAVAILABLE_CODE,
            "message": CUSTOM_RULE_UNAVAILABLE_DETAIL,
        }
    ]
    assert not [e for e in events if e["type"] == "answer_chunk"]
    assert chat.router_calls == 0
    assert chat.prompts == []
    assert ollama.calls[0][1]["options"]["num_ctx"] == ollama_client.OLLAMA_NUM_CTX


async def test_chat_turn_masks_the_query_with_the_rule_when_ollama_answers(
    chat: _ChatEnv, ollama: _FakeOllama
) -> None:
    events = await _ask(chat, f"When does {SECRET} ship?")

    assert not [e for e in events if e["type"] == "error"]
    assert chat.router_calls == 1
    assert SECRET not in chat.prompts[0]
    assert "[PROJECT_CODENAME_" in chat.prompts[0]
    assert {url.split("/api")[0] for url, _ in ollama.calls} == {"http://localhost:11500"}
    assert {body["model"] for _, body in ollama.calls} == {"rule-model:1b"}


async def test_stale_document_chunk_is_remasked_with_the_rule_before_the_cloud_call(
    chat: _ChatEnv, ollama: _FakeOllama
) -> None:
    events = await _ask(chat, "When did the milestone slip?")

    assert not [e for e in events if e["type"] == "error"]
    assert chat.router_calls == 1
    assert SECRET not in chat.prompts[0]
    assert "milestone slipped" in chat.prompts[0]
    assert "[PROJECT_CODENAME_" in chat.prompts[0]


async def test_stale_document_chunk_with_a_failing_rule_is_refused_not_sent_in_clear(
    chat: _ChatEnv, ollama: _FakeOllama
) -> None:
    ollama.error = _timeout()

    events = await _ask(chat, "When did the milestone slip?")

    assert [e["code"] for e in events if e["type"] == "error"] == [CUSTOM_RULE_UNAVAILABLE_CODE]
    assert chat.router_calls == 0
    assert chat.prompts == []


async def test_analyze_query_route_answers_503_with_the_code_when_the_rule_fails(
    chat: _ChatEnv, ollama: _FakeOllama
) -> None:
    ollama.error = _connect_error()

    resp = await chat.client.post(
        "/api/chat/analyze_query", json={"message": f"Where is {SECRET}?", "document_ids": []}
    )

    assert resp.status_code == 503
    assert resp.json() == {
        "detail": CUSTOM_RULE_UNAVAILABLE_DETAIL,
        "code": CUSTOM_RULE_UNAVAILABLE_CODE,
    }
    assert SECRET not in resp.text


async def test_ingestion_with_a_failing_rule_marks_the_document_failed_with_the_code(
    env, ollama: _FakeOllama, monkeypatch: pytest.MonkeyPatch
) -> None:
    ollama.error = _connect_error()
    await _store(env, _stored_settings(), _llm_rule())
    doc_id, _ = await env.add_document(status="processing")

    async def _ingest(**kwargs: Any) -> Any:
        return IngestionResult(
            text=f"Status report. The {SECRET} launch moves to next quarter.",
            confidence=1.0,
            metadata={},
        )

    monkeypatch.setattr(documents_module._INGESTION_ROUTER, "ingest", _ingest)
    source = documents_module._UploadedSource(env.tmp / "unused", "application/pdf", "pdf")

    await documents_module._run_upload_ingestion(doc_id, source, env.user.id, run_token=None)

    document = await env.document(doc_id)
    assert document is not None
    assert document.ingestion_status == "failed"
    assert document.ingestion_error_code == CUSTOM_RULE_UNAVAILABLE_CODE
    assert SECRET not in (document.ingestion_error or "")


async def _admin(client: Any) -> dict[str, str]:
    resp = await client.post(
        "/api/auth/register",
        json={"email": "rules-admin@example.com", "password": "password-12345"},
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _rule_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "Project Codename",
        "entity_type": "PROJECT_CODENAME",
        "detection_method": "llm_prompt",
        "llm_prompt": "Find internal project codenames.",
        "placeholder_label": "PROJECT_CODENAME",
    }
    body.update(overrides)
    return body


@pytest.mark.parametrize("prompt", [None, "", "   \n\t"], ids=["missing", "empty", "whitespace"])
async def test_creating_an_llm_prompt_rule_without_a_prompt_is_refused(
    router_client: Any, prompt: Any
) -> None:
    headers = await _admin(router_client)

    resp = await router_client.post(
        "/api/regulations/custom", headers=headers, json=_rule_body(llm_prompt=prompt)
    )

    assert (resp.status_code, resp.json().get("code")) == (422, "llm_prompt_required")
    listed = await router_client.get("/api/regulations/custom", headers=headers)
    assert listed.json() == []


async def test_updating_a_rule_to_an_llm_prompt_rule_without_a_prompt_is_refused(
    router_client: Any,
) -> None:
    headers = await _admin(router_client)
    llm = await router_client.post("/api/regulations/custom", headers=headers, json=_rule_body())
    regex = await router_client.post(
        "/api/regulations/custom",
        headers=headers,
        json=_rule_body(
            name="File",
            entity_type="FILE_NO",
            detection_method="regex",
            pattern=r"F-\d+",
            llm_prompt=None,
            placeholder_label="FILE_NO",
        ),
    )
    assert (llm.status_code, regex.status_code) == (201, 201)
    llm_url = f"/api/regulations/custom/{llm.json()['id']}"

    emptied = await router_client.patch(llm_url, headers=headers, json={"llm_prompt": "  "})
    switched = await router_client.patch(
        f"/api/regulations/custom/{regex.json()['id']}",
        headers=headers,
        json={"detection_method": "llm_prompt"},
    )
    renamed = await router_client.patch(llm_url, headers=headers, json={"name": "Codename"})

    assert (emptied.status_code, emptied.json().get("code")) == (422, "llm_prompt_required")
    assert (switched.status_code, switched.json().get("code")) == (422, "llm_prompt_required")
    assert renamed.status_code == 200
    assert renamed.json()["llm_prompt"] == "Find internal project codenames."


async def test_documents_masked_before_the_context_gate_become_outdated(env) -> None:
    """A regex rule with context words moves the digest off the one recorded before the gate."""
    rule = _regex_rule()
    await _store(env, rule)
    defaults = build_default_app_settings()
    pre_gate = build_policy_snapshot(
        [], [_pre_fix_definition(rule)], [], detection=detection_settings(defaults)
    )

    async with env.session_maker() as session:
        read = await current_policy_snapshot(session)
        _, composed = await PolicyComposer().compose_with_snapshot(session, defaults)

    assert read.digest == composed.digest
    assert read.digest != pre_gate.digest


GATED_VALUE = "PFN-123456"


async def test_chat_query_masks_a_gated_rule_match_without_context(env) -> None:
    await _store(env, _regex_rule())
    anon_map = AnonymizationMap(document_id=0, language="en")

    async with env.session_maker() as session:
        sanitized, count, _ = await chat_module._sanitize_query(
            f"Who owns {GATED_VALUE}?", "en", _settings(), anon_map, session
        )

    assert GATED_VALUE not in sanitized
    assert count == 1


async def test_document_sanitizer_keeps_the_context_gate(env) -> None:
    await _store(env, _regex_rule())

    async with env.session_maker() as session:
        sanitizer = await create_sanitizer(session, _settings())
    without = sanitizer.sanitize(
        f"Who owns {GATED_VALUE}?", "en", AnonymizationMap(document_id=1, language="en")
    )
    with_context = sanitizer.sanitize(
        f"The file {GATED_VALUE} is closed.", "en", AnonymizationMap(document_id=1, language="en")
    )

    assert GATED_VALUE in without.sanitized_text
    assert GATED_VALUE not in with_context.sanitized_text


async def test_edited_approval_chunk_masks_a_gated_rule_match_without_context(env) -> None:
    await _store(env, _regex_rule())
    anon_map = AnonymizationMap(document_id=0, language="en")

    async with env.session_maker() as session:
        [gated] = await chat_module._gate_edited_chunks(
            ["Offered chunk."], [f"Who owns {GATED_VALUE}?"], anon_map, session, _settings()
        )

    assert GATED_VALUE not in gated


async def test_column_labels_and_ruleset_names_mask_a_gated_rule_match_without_context(
    env,
) -> None:
    await _store(env, _regex_rule())
    anon_map = AnonymizationMap(document_id=0, language="en")

    async with env.session_maker() as session:
        labels = await gate_untrusted_texts(
            [f"Owner {GATED_VALUE}", "Amount"], anon_map, session, _settings(), joined=True
        )
        names = await chat_module._prompt_regulation_names(
            [("GDPR", True), (f"Rules {GATED_VALUE}", False)], anon_map, session, _settings()
        )

    assert GATED_VALUE not in labels[0]
    assert labels[1] == "Amount"
    assert names[0] == "GDPR"
    assert GATED_VALUE not in names[1]


async def test_chunk_edits_mask_a_gated_rule_match_without_context(env) -> None:
    await _store(env, _regex_rule())
    doc_id, _ = await env.add_document()

    async with env.session_maker() as session:
        document = await session.get(Document, doc_id)
        [gated] = await pii_gate.gate_document_texts(
            session, _settings(), document, [f"Owner {GATED_VALUE}"]
        )

    assert GATED_VALUE not in gated


async def test_outdated_chunk_remask_masks_a_gated_rule_match_without_context(env) -> None:
    await _store(env, _regex_rule())
    doc_id, _ = await env.add_document()
    await document_anon_store.set_document_map(
        doc_id, AnonymizationMap(document_id=doc_id, language="en")
    )
    remask_cache.clear()

    async with env.session_maker() as session:
        document = await session.get(Document, doc_id)
        chunk = (
            await session.execute(select(Chunk).where(Chunk.document_id == doc_id))
        ).scalar_one()
        chunk.sanitized_text = f"Owner {GATED_VALUE}"
        restored, changed = await chat_module._resanitize_outdated_chunks(
            session, _settings(), [chunk], {doc_id: document}
        )
    remask_cache.clear()

    assert GATED_VALUE not in restored[chunk.id]
    assert "[PATIENT_FILE_NUMBER_" in restored[chunk.id]
    assert changed == {doc_id}


async def test_user_text_gates_refuse_a_context_gated_sanitizer(env) -> None:
    await _store(env, _regex_rule())
    doc_id, _ = await env.add_document()
    text = f"Owner {GATED_VALUE}"

    async with env.session_maker() as session:
        gated = await create_sanitizer(session, _settings())
        ungated = await pii_gate.create_user_text_sanitizer(session, _settings())
        document = await session.get(Document, doc_id)
        with pytest.raises(ValueError):
            await gate_untrusted_texts(
                [text], AnonymizationMap(document_id=0, language="en"), session, _settings(),
                sanitizer=gated,
            )
        with pytest.raises(ValueError):
            await pii_gate.gate_document_texts(
                session, _settings(), document, [text], sanitizer=gated
            )
        with pytest.raises(ValueError):
            await pii_gate.remask_document_texts(session, _settings(), document, [text], gated)
        assert await document_anon_store.load_persisted_document_map(doc_id) is None
        [masked] = await gate_untrusted_texts(
            [text], AnonymizationMap(document_id=0, language="en"), session, _settings(),
            sanitizer=ungated,
        )

    assert (gated.context_gate, ungated.context_gate) == (True, False)
    assert GATED_VALUE not in masked


async def test_user_text_gates_refuse_a_sanitizer_without_a_gate_flag() -> None:
    anon_map = AnonymizationMap(document_id=0, language="en")
    with pytest.raises(ValueError):
        await gate_untrusted_texts([f"Owner {GATED_VALUE}"], anon_map, None, None, sanitizer=object())
    document = SimpleNamespace(id=1)
    with pytest.raises(ValueError):
        await pii_gate.remask_document_texts(None, None, document, ["text"], object())
    assert anon_map.entity_map == {}


async def test_pure_chat_turn_carries_no_ruleset_name_into_the_prompt_fields() -> None:
    """Without document context nothing but the query is sent, so no admin-typed name is staged."""
    sentinel = "Zorblax Ruleset"
    turn = chat_module._ChatTurn(
        request=SimpleNamespace(output_mode=None),
        message_text="hello",
        user=SimpleNamespace(id=1),
        session_id="s",
        debug_id="d",
        settings=_settings(),
        active_regulations=[("GDPR", True), (sentinel, False)],
        documents_by_id={},
        document=None,
        language="en",
        query_seed_document_id=None,
        anon_map=AnonymizationMap(document_id=0, language="en"),
        rag_mode="none",
        sanitized_query="hello",
    )

    await chat_module._prepare_prompt_inputs(turn, None)

    assert turn.prompt_regulation_names == []
    assert sentinel in turn.regulation_names
