"""Bounds of the local-model detection runner: parallelism, cache lifetime, cache keys,
cancellation, the per-turn budget on the real chat path and the label gate's call sites.

Every Ollama call is a double; no ordering relies on a sleep.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any, Callable, Iterator, List, Optional

import pytest
from septum_api.errors import (
    LOCAL_MODEL_BUDGET_EXCEEDED_CODE,
    SemanticLayerUnavailableError,
)
from septum_api.request_limits import MAX_OLLAMA_CALLS_PER_TURN
from septum_api.services import ollama_call_budget as budget_module
from septum_api.services import ollama_client, ollama_detection_runner as runner
from septum_api.services import sanitizer as sanitizer_module
from septum_api.services.ollama_call_budget import ollama_call_budget
from septum_api.services.ollama_detection_runner import (
    CACHE_MAX_ENTRIES,
    CACHE_TTL_SECONDS,
    DetectionCall,
    detection_cache_scope,
    run_detection_calls,
)
from septum_api.services.prompts import DetectionPrompt
from septum_api.services.recognizers import registry as registry_module
from septum_api.services.recognizers.registry import LLMContextConfig, LLMContextRecognizer


@pytest.fixture(autouse=True)
def _user_scope() -> Iterator[None]:
    with detection_cache_scope(1, "epoch-1"):
        yield


def _call(
    text: str, base_url: Optional[str] = None, model: Optional[str] = None, system: str = "s"
) -> DetectionCall[str]:
    return DetectionCall(
        prompt=lambda nonce: DetectionPrompt(system, f"<data-{nonce}>\n{text}\n</data-{nonce}>"),
        parse=lambda reply: reply,
        base_url=base_url,
        model=model,
    )


def _calls(count: int) -> List[DetectionCall[str]]:
    return [_call(f"text {i}") for i in range(count)]


def _text_of(prompt: str) -> str:
    return prompt.split("\n")[1]


class _Sender:
    def __init__(self) -> None:
        self.sent: List[dict[str, Any]] = []
        self._lock = threading.Lock()

    def __call__(self, **kwargs: Any) -> str:
        with self._lock:
            self.sent.append(kwargs)
        return _text_of(kwargs["prompt"])


def _run(calls: List[DetectionCall[str]], send: Callable[..., str]) -> List[str]:
    return run_detection_calls(calls, send=send, refusal=sanitizer_module._contextual_refusal)


@pytest.fixture
def parallel(monkeypatch: pytest.MonkeyPatch) -> Callable[[int], None]:
    """Give the runner ``n`` slots and a pool wider than that."""

    pools: List[ThreadPoolExecutor] = []

    def _set(slots: int) -> None:
        pool = ThreadPoolExecutor(max_workers=8)
        pools.append(pool)
        monkeypatch.setattr(runner, "_slots", threading.BoundedSemaphore(slots))
        monkeypatch.setattr(runner, "_pool", pool)

    yield _set
    for pool in pools:
        pool.shutdown(wait=True, cancel_futures=True)


# ---------------------------------------------------------------- parallelism setting


def test_the_parallelism_defaults_to_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OLLAMA_NUM_PARALLEL", raising=False)

    assert runner._parallelism() == 1


@pytest.mark.parametrize("raw, expected", [("1", 1), ("4", 4), ("16", 16), (" 8 ", 8)])
def test_a_parallelism_from_one_to_sixteen_is_accepted(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: int
) -> None:
    monkeypatch.setenv("OLLAMA_NUM_PARALLEL", raw)

    assert runner._parallelism() == expected


@pytest.mark.parametrize("raw", ["0", "17", "-3", "abc", "2.5", "", "   "])
def test_an_unusable_parallelism_falls_back_to_one(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("OLLAMA_NUM_PARALLEL", raw)

    assert runner._parallelism() == 1


def test_calls_in_flight_never_exceed_the_slot_count(parallel: Callable[[int], None]) -> None:
    parallel(3)
    lock = threading.Lock()
    full = threading.Event()
    state = {"now": 0, "peak": 0}

    def _send(**kwargs: Any) -> str:
        with lock:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
            if state["now"] == 3:
                full.set()
        assert full.wait(5)
        with lock:
            state["now"] -= 1
        return _text_of(kwargs["prompt"])

    assert _run(_calls(9), _send) == [f"text {i}" for i in range(9)]
    assert state["peak"] == 3


# ---------------------------------------------------------------- cache lifetime


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    now = SimpleNamespace(value=1000.0)
    monkeypatch.setattr(runner, "time", SimpleNamespace(monotonic=lambda: now.value))
    return now


def test_a_cached_reply_expires_after_its_ttl(clock: SimpleNamespace) -> None:
    send = _Sender()
    _run([_call("kept")], send)

    clock.value += CACHE_TTL_SECONDS - 1
    _run([_call("kept")], send)
    assert len(send.sent) == 1

    clock.value += 1
    _run([_call("kept")], send)
    assert len(send.sent) == 2


def test_a_write_drops_every_expired_entry(clock: SimpleNamespace) -> None:
    send = _Sender()
    _run([_call("old a"), _call("old b")], send)
    clock.value += CACHE_TTL_SECONDS

    _run([_call("fresh")], send)

    assert len(runner._cache) == 1


def test_a_write_under_a_new_epoch_drops_the_entries_of_the_old_one() -> None:
    send = _Sender()
    _run([_call("before a"), _call("before b")], send)

    with detection_cache_scope(1, "epoch-2"):
        _run([_call("after")], send)

    assert len(runner._cache) == 1


def test_a_read_under_a_new_epoch_clears_the_local_cache() -> None:
    send = _Sender()
    _run([_call("before a"), _call("before b")], send)

    found, _ = runner._cache.get(("user:9", "", "", "x"), "epoch-2")

    assert not found
    assert len(runner._cache) == 0


def test_the_cache_holds_at_most_the_entry_limit_and_evicts_the_least_recently_used() -> None:
    assert CACHE_MAX_ENTRIES == 1024
    send = _Sender()
    for i in range(CACHE_MAX_ENTRIES):
        _run([_call(f"entry {i}")], send)
    _run([_call("entry 0")], send)
    assert len(send.sent) == CACHE_MAX_ENTRIES

    _run([_call("one more")], send)

    assert len(runner._cache) == CACHE_MAX_ENTRIES
    before = len(send.sent)
    _run([_call("entry 0")], send)
    assert len(send.sent) == before
    _run([_call("entry 1")], send)
    assert len(send.sent) == before + 1


# ---------------------------------------------------------------- cache keys


def test_another_model_is_sent_again() -> None:
    send = _Sender()
    _run([_call("same", "http://a:1", "model-a")], send)
    _run([_call("same", "http://a:1", "model-b")], send)
    _run([_call("same", "http://a:1", "model-a")], send)

    assert len(send.sent) == 2


def test_another_server_is_sent_again() -> None:
    send = _Sender()
    _run([_call("same", "http://a:1", "m")], send)
    _run([_call("same", "http://b:1", "m")], send)

    assert len(send.sent) == 2


def test_another_user_is_sent_again() -> None:
    send = _Sender()
    _run([_call("same")], send)
    with detection_cache_scope(2, "epoch-1"):
        _run([_call("same")], send)

    assert len(send.sent) == 2


def test_another_system_prompt_is_sent_again() -> None:
    send = _Sender()
    _run([_call("same", system="one")], send)
    _run([_call("same", system="two")], send)

    assert len(send.sent) == 2


def test_a_changed_rule_prompt_is_sent_again(monkeypatch: pytest.MonkeyPatch) -> None:
    send = _Sender()
    monkeypatch.setattr(registry_module, "request_ollama_sync", lambda **kw: send(**kw) and "[]")
    monkeypatch.setattr(
        registry_module, "ollama_context_window", lambda *_: ollama_client.OLLAMA_NUM_CTX
    )

    def _analyze(prompt: str) -> None:
        LLMContextRecognizer(
            LLMContextConfig(
                name="r", entity_type="CUSTOM_ID", llm_prompt=prompt, context_words=[], rule_id=7
            )
        ).analyze("Code QX-4471 here.")

    _analyze("find ids")
    _analyze("find ids")
    assert len(send.sent) == 1

    _analyze("find codes")
    assert len(send.sent) == 2


def test_a_cached_reply_is_a_copy_the_caller_cannot_change() -> None:
    calls = [
        DetectionCall(
            prompt=lambda nonce: DetectionPrompt("s", f"<data-{nonce}>\nx\n</data-{nonce}>"),
            parse=lambda reply: [{"text": reply}],
            base_url=None,
            model=None,
        )
    ]
    send = _Sender()

    first = run_detection_calls(calls, send=send, refusal=RuntimeError)
    first[0][0]["text"] = "changed"
    first[0].append({"text": "extra"})
    second = run_detection_calls(calls, send=send, refusal=RuntimeError)

    assert len(send.sent) == 1
    assert second == [[{"text": "x"}]]


# ---------------------------------------------------------------- cancellation


def test_a_cancelled_future_is_the_cancelled_failure_and_never_hangs() -> None:
    cancelled: Future[str] = Future()
    cancelled.cancel()
    failed: Future[str] = Future()
    failed.set_exception(runner._CallFailed("ReadTimeout"))

    runner._wait_for_first_failure([cancelled, Future()])

    error = runner._first_failure([cancelled])
    assert isinstance(error, runner._CallFailed) and error.kind == runner.CANCELLED_CALL
    own = runner._first_failure([cancelled, failed])
    assert isinstance(own, runner._CallFailed) and own.kind == "ReadTimeout"


def test_a_cancelled_dispatch_refuses_with_its_code_and_returns_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _CancellingPool:
        def submit(self, *args: Any) -> Future:
            future: Future = Future()
            future.cancel()
            return future

    monkeypatch.setattr(runner, "_pool", _CancellingPool())
    send = _Sender()

    with pytest.raises(SemanticLayerUnavailableError) as raised:
        _run(_calls(3), send)

    assert runner.CANCELLED_CALL in str(raised.value)
    assert send.sent == []
    assert len(runner._cache) == 0


# ---------------------------------------------------------------- parallel mode


def test_the_budget_is_visible_in_pool_threads_and_counts_every_call(
    parallel: Callable[[int], None],
) -> None:
    parallel(4)
    seen: List[tuple[Any, threading.Thread]] = []
    lock = threading.Lock()

    def _send(**kwargs: Any) -> str:
        with lock:
            seen.append((budget_module._current_budget.get(), threading.current_thread()))
        return _text_of(kwargs["prompt"])

    with ollama_call_budget(limit=6) as budget:
        _run(_calls(6), _send)

    assert budget.used == 6
    assert len(seen) == 6
    assert all(found is budget for found, _ in seen)
    assert all(thread is not threading.main_thread() for _, thread in seen)


def test_two_racing_failures_are_one_refusal_and_no_partial_result(
    parallel: Callable[[int], None],
) -> None:
    parallel(4)
    barrier = threading.Barrier(4)

    def _send(**kwargs: Any) -> str:
        text = _text_of(kwargs["prompt"])
        barrier.wait(5)
        if text in ("text 0", "text 1"):
            raise ollama_client.OllamaCallError("ReadTimeout")
        return text

    outcomes: List[Any] = []
    try:
        outcomes.append(_run(_calls(4), _send))
    except SemanticLayerUnavailableError as exc:
        outcomes.append(exc)

    assert len(outcomes) == 1
    assert isinstance(outcomes[0], SemanticLayerUnavailableError)
    assert "ReadTimeout" in str(outcomes[0])
    assert len(runner._cache) == 0


# ---------------------------------------------------------------- the real chat path


class _Reached(Exception):
    pass


def _sse(event: bytes) -> dict[str, Any]:
    return json.loads(event.decode("utf-8").removeprefix("data: ").strip())


@pytest.fixture
def chat_turn(monkeypatch: pytest.MonkeyPatch) -> Callable[[int], Any]:
    """Run the real SSE handler; the query step makes ``count`` distinct detection calls."""
    from septum_api.routers import chat as chat_module

    sent = _Sender()
    wanted = {"count": 0}

    async def _open_turn(*args: Any) -> Any:
        return SimpleNamespace(
            session_id="s", message_text="hi", language="en", settings=None, anon_map=None
        )

    async def _sanitize_query(*args: Any, **kwargs: Any) -> Any:
        _run(_calls(wanted["count"]), sent)
        return ("hi", 0, {})

    async def _next_step(*args: Any) -> None:
        raise _Reached()

    async def _recorded(*args: Any) -> None:
        return None

    async def _unexpected(exc: Exception, *args: Any) -> dict[str, Any]:
        return {"type": "error", "code": type(exc).__name__}

    monkeypatch.setattr(chat_module, "_open_turn", _open_turn)
    monkeypatch.setattr(chat_module, "_sanitize_query", _sanitize_query)
    monkeypatch.setattr(chat_module, "_select_turn_context", _next_step)
    monkeypatch.setattr(chat_module, "_record_backend_error", _recorded)
    monkeypatch.setattr(chat_module, "_unexpected_error_event", _unexpected)

    async def _play(count: int) -> Any:
        wanted["count"] = count
        request = chat_module.ChatRequest(message="hi")
        stream = chat_module._chat_event_stream(
            request, "hi", [], {}, {}, None, SimpleNamespace(id=42), None
        )
        events = [_sse(event) async for event in stream]
        return events, sent

    return _play


async def test_a_turn_over_the_call_budget_is_the_coded_error_event(chat_turn: Any) -> None:
    events, sent = await chat_turn(MAX_OLLAMA_CALLS_PER_TURN + 1)

    assert [e["code"] for e in events if e["type"] == "error"] == [LOCAL_MODEL_BUDGET_EXCEEDED_CODE]
    assert sent.sent == []


async def test_a_turn_at_exactly_the_call_budget_goes_on(chat_turn: Any) -> None:
    events, sent = await chat_turn(MAX_OLLAMA_CALLS_PER_TURN)

    assert len(sent.sent) == MAX_OLLAMA_CALLS_PER_TURN == 128
    assert [e["code"] for e in events if e["type"] == "error"] == ["_Reached"]


# ---------------------------------------------------------------- label gate call sites


async def test_the_schema_update_gates_its_labels_joined(monkeypatch: pytest.MonkeyPatch) -> None:
    from septum_api.routers import documents as documents_module

    seen: dict[str, Any] = {}

    async def _gate(db: Any, settings: Any, document: Any, texts: List[str], **kwargs: Any) -> List[str]:
        seen.update(kwargs)
        return [f"gated {t}" for t in texts]

    async def _settings(db: Any) -> Any:
        return None

    monkeypatch.setattr(documents_module, "gate_document_texts", _gate)
    monkeypatch.setattr(documents_module, "load_settings", _settings)
    payload = documents_module.SpreadsheetSchemaUpdatePayload(
        columns=[documents_module.SpreadsheetColumnUpdate(index=0, technical_label="a", semantic_label="Owner")]
    )

    await documents_module._gate_semantic_labels(None, SimpleNamespace(), payload)

    assert seen == {"joined": True}
    assert payload.columns[0].semantic_label == "gated Owner"


async def test_the_chat_schema_description_gates_its_labels_joined(monkeypatch: pytest.MonkeyPatch) -> None:
    from septum_api.routers import chat as chat_module

    seen: dict[str, Any] = {}
    columns = [
        SimpleNamespace(index=0, semantic_label="Owner", is_numeric=False),
        SimpleNamespace(index=1, semantic_label="Rate", is_numeric=True),
    ]

    class _Result:
        def scalar_one_or_none(self) -> Any:
            return SimpleNamespace(columns=columns)

    class _Db:
        async def execute(self, statement: Any) -> _Result:
            return _Result()

    async def _gate(texts: List[str], anon_map: Any, db: Any, settings: Any, **kwargs: Any) -> List[str]:
        seen.update(kwargs)
        return [f"gated {t}" for t in texts]

    monkeypatch.setattr(chat_module, "gate_untrusted_texts", _gate)

    described = await chat_module._schema_column_descriptions(_Db(), 1, None, None)

    assert seen == {"joined": True}
    assert described[0].endswith("gated Owner")
    assert described[1].endswith("gated Rate (numeric)")
