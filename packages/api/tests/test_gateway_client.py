"""Producer-side GatewayClient tests using FileQueueBackend end-to-end."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from septum_api.models.settings import AppSettings
from septum_api.services import gateway_client as gateway_module
from septum_api.services.gateway_client import (
    CLIENT_TIMEOUT_ENV,
    DEFAULT_CLIENT_TIMEOUT_SECONDS,
    GATEWAY_FAILED_MESSAGE,
    GATEWAY_INVALID_REPLY_MESSAGE,
    MAX_BLOCK_MS,
    GatewayClient,
    client_timeout_from_env,
)
from septum_api.services.llm_errors import (
    LLM_UNAVAILABLE,
    GatewayStartupError,
    LLMRouterError,
)
from septum_gateway.forwarder import _MAX_RETRY_AFTER_SECONDS, BaseForwarder
from septum_queue import (
    FileQueueBackend,
    QueueTimeoutError,
    RequestEnvelope,
    ResponseEnvelope,
)


def _settings(**overrides) -> AppSettings:
    defaults = dict(
        id=1,
        llm_provider="anthropic",
        llm_model="claude-sonnet-4-20250514",
        ollama_base_url="http://localhost:11434",
        ollama_chat_model="llama3.2:3b",
        ollama_deanon_model="llama3.2:3b",
        deanon_enabled=True,
        deanon_strategy="simple",
        require_approval=True,
        show_json_output=False,
        use_presidio_layer=True,
        use_ner_layer=True,
        use_ollama_validation_layer=True,
        use_ollama_layer=True,
        use_ollama_semantic_layer=False,
        chunk_size=800,
        chunk_overlap=200,
        top_k_retrieval=5,
        pdf_chunk_size=1200,
        audio_chunk_size=60,
        spreadsheet_chunk_size=200,
        whisper_model="base",
        default_audio_language=None,
        image_ocr_languages=["en"],
        ocr_provider="paddleocr",
        ocr_provider_options=None,
        extract_embedded_images=True,
        recursive_email_attachments=True,
        default_active_regulations=["gdpr"],
        ner_model_overrides=None,
        anthropic_api_key="sk-test-anth",
        openai_api_key=None,
        openrouter_api_key=None,
        setup_completed=True,
        use_gateway=True,
    )
    defaults.update(overrides)
    return AppSettings(**defaults)


async def _fake_gateway(request_queue: FileQueueBackend, response_queue: FileQueueBackend, *, reply):
    """Stand-in for the real GatewayConsumer that produces a fixed reply.

    ``reply`` is a callable taking the request envelope and returning
    a ``ResponseEnvelope``. The helper consumes one request and stops.
    """
    async for message in request_queue.consume(batch_size=1, block_ms=2000):
        request = RequestEnvelope.from_dict(message.payload)
        response = reply(request)
        await response_queue.publish(response.to_dict())
        await request_queue.ack(message.id)
        return


class _NoopQueue:
    """Queue stand-in whose calls return at once; subclasses override what they need."""

    async def publish(self, payload) -> str:
        return "entry-id"

    async def consume(self, *, batch_size: int, block_ms: int):
        return
        yield

    async def ack(self, message_id: str) -> None:
        return None

    async def nack(self, message_id: str, *, requeue: bool = True) -> None:
        return None

    async def close(self) -> None:
        return None


class _HangingQueue(_NoopQueue):
    """Publish or consume never returns while ``hang`` is set; the gate is never set."""

    def __init__(self, *, hang: bool) -> None:
        self._hang = hang
        self._gate = asyncio.Event()

    async def publish(self, payload) -> str:
        if self._hang:
            await self._gate.wait()
        return "entry-id"

    async def consume(self, *, batch_size: int, block_ms: int):
        if self._hang:
            await self._gate.wait()
        return
        yield


class _SlowReadQueue(_NoopQueue):
    """Read ends by itself after ``block_ms``; records any cancellation."""

    def __init__(self) -> None:
        self.reads = 0
        self.cancelled = False

    async def consume(self, *, batch_size: int, block_ms: int):
        self.reads += 1
        try:
            await asyncio.sleep(block_ms / 1000)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return
        yield


class _RaisingReadQueue(_NoopQueue):
    """Read fails with a builtin TimeoutError, not a queue timeout."""

    async def consume(self, *, batch_size: int, block_ms: int):
        raise TimeoutError("socket stalled")
        yield


class TestGatewayClient:
    async def test_complete_round_trip_returns_text(self, tmp_path: Path):
        req_q = FileQueueBackend(tmp_path, topic="req")
        resp_q = FileQueueBackend(tmp_path, topic="resp")
        client = GatewayClient(
            request_queue=req_q, response_queue=resp_q, timeout_seconds=3.0
        )
        try:
            settings = _settings()

            def reply(request):
                return ResponseEnvelope(
                    correlation_id=request.correlation_id,
                    text="[PERSON_1] is in [CITY_1].",
                    provider=request.provider,
                    model=request.model,
                )

            fake = asyncio.create_task(_fake_gateway(req_q, resp_q, reply=reply))
            result = await client.complete(
                settings=settings,
                messages=[{"role": "user", "content": "Where is [PERSON_1]?"}],
            )
            await fake
            assert result == "[PERSON_1] is in [CITY_1]."
        finally:
            await req_q.close()
            await resp_q.close()

    async def test_gateway_error_maps_to_llm_router_error(self, tmp_path: Path):
        req_q = FileQueueBackend(tmp_path, topic="req")
        resp_q = FileQueueBackend(tmp_path, topic="resp")
        client = GatewayClient(
            request_queue=req_q, response_queue=resp_q, timeout_seconds=3.0
        )
        try:
            def reply(request):
                return ResponseEnvelope(
                    correlation_id=request.correlation_id,
                    error="upstream 502",
                    provider=request.provider,
                )

            fake = asyncio.create_task(_fake_gateway(req_q, resp_q, reply=reply))
            with pytest.raises(LLMRouterError) as exc_info:
                await client.complete(
                    settings=_settings(),
                    messages=[{"role": "user", "content": "hi"}],
                )
            await fake
            assert str(exc_info.value) == GATEWAY_FAILED_MESSAGE
            assert exc_info.value.code == LLM_UNAVAILABLE
        finally:
            await req_q.close()
            await resp_q.close()

    async def test_missing_reply_times_out(self, tmp_path: Path):
        req_q = FileQueueBackend(tmp_path, topic="req")
        resp_q = FileQueueBackend(tmp_path, topic="resp")
        client = GatewayClient(
            request_queue=req_q, response_queue=resp_q, timeout_seconds=0.3
        )
        try:
            with pytest.raises(QueueTimeoutError):
                await client.complete(
                    settings=_settings(),
                    messages=[{"role": "user", "content": "hi"}],
                )
        finally:
            await req_q.close()
            await resp_q.close()

    async def test_envelope_does_not_carry_api_key(
        self, tmp_path: Path
    ):
        """Cloud credentials are owned by the gateway zone — envelopes are secret-free."""
        req_q = FileQueueBackend(tmp_path, topic="req")
        resp_q = FileQueueBackend(tmp_path, topic="resp")
        client = GatewayClient(
            request_queue=req_q, response_queue=resp_q, timeout_seconds=3.0
        )
        captured: list[RequestEnvelope] = []
        try:
            def reply(request):
                captured.append(request)
                return ResponseEnvelope(
                    correlation_id=request.correlation_id,
                    text="ok",
                )

            fake = asyncio.create_task(_fake_gateway(req_q, resp_q, reply=reply))
            await client.complete(
                settings=_settings(
                    llm_provider="openai",
                    openai_api_key="sk-openai-from-api",
                    anthropic_api_key=None,
                ),
                messages=[{"role": "user", "content": "hi"}],
            )
            await fake
            assert captured[0].provider == "openai"
            # The producer-side api_key field is no longer placed on
            # the queue — the gateway loads the credential from its
            # own SEPTUM_GATEWAY_*_API_KEY env vars.
            assert "api_key" not in captured[0].to_dict()
        finally:
            await req_q.close()
            await resp_q.close()


class TestLLMRouterGatewayBranch:
    """LLMRouter delegates to the gateway factory when use_gateway=True."""

    async def test_use_gateway_false_keeps_direct_call_path(self):
        """The default path must stay untouched so existing tests keep passing."""
        from septum_api.services import llm_router

        assert llm_router._gateway_client_factory is None
        llm_router.set_gateway_client_factory(None)
        assert llm_router._gateway_client_factory is None

    async def test_factory_registration_round_trip(self):
        from septum_api.services import llm_router

        sentinel = object()

        def factory(_settings):
            return sentinel

        try:
            llm_router.set_gateway_client_factory(factory)
            assert llm_router._gateway_client_factory is factory
        finally:
            llm_router.set_gateway_client_factory(None)
        assert llm_router._gateway_client_factory is None


ORPHAN_TEXT = "orphan-sentinel-[PERSON_9]"


async def _drain(queue: FileQueueBackend) -> list[ResponseEnvelope]:
    left: list[ResponseEnvelope] = []
    async for message in queue.consume(batch_size=16, block_ms=0):
        left.append(ResponseEnvelope.from_dict(message.payload))
        await queue.ack(message.id)
    return left


class TestOrphanReplies:
    """Replies nobody waits for any more are dropped; recent ones stay queued."""

    async def _complete_with_stray(self, tmp_path: Path, *, stray_age: float) -> list[ResponseEnvelope]:
        req_q = FileQueueBackend(tmp_path, topic="req")
        resp_q = FileQueueBackend(tmp_path, topic="resp")
        client = GatewayClient(request_queue=req_q, response_queue=resp_q, timeout_seconds=1.0)
        try:
            await resp_q.publish(
                ResponseEnvelope(
                    correlation_id="stray",
                    text=ORPHAN_TEXT,
                    created_at=time.time() - stray_age,
                ).to_dict()
            )

            async def _delayed_gateway() -> None:
                await asyncio.sleep(0.2)
                await _fake_gateway(
                    req_q,
                    resp_q,
                    reply=lambda request: ResponseEnvelope(
                        correlation_id=request.correlation_id, text="ok"
                    ),
                )

            fake = asyncio.create_task(_delayed_gateway())
            assert await client.complete(
                settings=_settings(), messages=[{"role": "user", "content": "hi"}]
            ) == "ok"
            await fake
            return await _drain(resp_q)
        finally:
            await req_q.close()
            await resp_q.close()

    async def test_reply_older_than_twice_the_timeout_is_acked_and_dropped(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ):
        with caplog.at_level(logging.DEBUG):
            left = await self._complete_with_stray(tmp_path, stray_age=2.5)

        assert left == []
        assert "orphaned gateway reply" in caplog.text
        assert ORPHAN_TEXT not in caplog.text
        assert "stray" not in caplog.text

    async def test_recent_reply_for_another_waiter_is_requeued(self, tmp_path: Path):
        left = await self._complete_with_stray(tmp_path, stray_age=0.0)

        assert [envelope.correlation_id for envelope in left] == ["stray"]

    async def test_reply_past_one_timeout_but_within_two_is_still_requeued(self, tmp_path: Path):
        """The cutoff is 2x the timeout (1.0 s here), not 1x."""
        left = await self._complete_with_stray(tmp_path, stray_age=1.5)

        assert [envelope.correlation_id for envelope in left] == ["stray"]


class _FailingCloseQueue:
    def __init__(self, error: Exception | None = None) -> None:
        self.closed = False
        self._error = error

    async def close(self) -> None:
        self.closed = True
        if self._error is not None:
            raise self._error


class TestAclose:
    async def test_response_queue_is_closed_when_request_close_fails(self):
        request_queue = _FailingCloseQueue(RuntimeError("request close failed"))
        response_queue = _FailingCloseQueue()
        client = GatewayClient(request_queue=request_queue, response_queue=response_queue)

        with pytest.raises(RuntimeError, match="request close failed"):
            await client.aclose()

        assert request_queue.closed and response_queue.closed

    async def test_response_close_error_propagates_after_request_is_closed(self):
        request_queue = _FailingCloseQueue()
        response_queue = _FailingCloseQueue(RuntimeError("response close failed"))
        client = GatewayClient(request_queue=request_queue, response_queue=response_queue)

        with pytest.raises(RuntimeError, match="response close failed"):
            await client.aclose()

        assert request_queue.closed and response_queue.closed


class TestBuildFromEnvPartialFailure:
    async def test_request_backend_is_closed_when_response_backend_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        from septum_api.services import gateway_client as module

        opened = _FailingCloseQueue()

        def _backend(topic: str):
            if topic == "bad.topic":
                raise ValueError("invalid topic")
            return opened

        monkeypatch.setattr(module, "backend_from_env", _backend)
        monkeypatch.setenv(module.REQUEST_TOPIC_ENV, "good.topic")
        monkeypatch.setenv(module.RESPONSE_TOPIC_ENV, "bad.topic")

        with pytest.raises(GatewayStartupError):
            await module.build_gateway_client_from_env()

        assert opened.closed


class TestClientTimeoutEnv:
    def test_default_covers_gateway_worst_case(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv(CLIENT_TIMEOUT_ENV, raising=False)
        forwarder_defaults = inspect.signature(BaseForwarder.__init__).parameters
        attempts = forwarder_defaults["max_attempts"].default
        attempt_timeout = forwarder_defaults["timeout_seconds"].default
        base_backoff = forwarder_defaults["base_backoff_seconds"].default
        waits = attempts - 1
        worst_case = attempts * attempt_timeout + waits * max(
            _MAX_RETRY_AFTER_SECONDS, base_backoff * 2 ** (attempts - 2)
        )

        assert client_timeout_from_env() == DEFAULT_CLIENT_TIMEOUT_SECONDS
        assert worst_case > 0
        assert DEFAULT_CLIENT_TIMEOUT_SECONDS > worst_case, (
            f"api client timeout {DEFAULT_CLIENT_TIMEOUT_SECONDS}s must exceed the "
            f"gateway forwarder worst case {worst_case}s"
        )
        assert GatewayClient(request_queue=None, response_queue=None)._timeout_seconds == (
            DEFAULT_CLIENT_TIMEOUT_SECONDS
        )

    def test_override_is_used(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(CLIENT_TIMEOUT_ENV, "45")

        assert client_timeout_from_env() == 45.0

    @pytest.mark.parametrize("raw", ["soon", "0", "-5", "nan", "inf"])
    def test_unusable_value_is_refused(self, monkeypatch: pytest.MonkeyPatch, raw: str):
        monkeypatch.setenv(CLIENT_TIMEOUT_ENV, raw)

        with pytest.raises(GatewayStartupError, match=CLIENT_TIMEOUT_ENV):
            client_timeout_from_env()


GATEWAY_TEXT_SENTINEL = "provider-said-[PERSON_7]-sk-leak"


class TestGatewayErrorText:
    """Gateway error text never reaches the raised error or the log."""

    @pytest.mark.parametrize(
        ("fields", "expected"),
        [
            ({"error": GATEWAY_TEXT_SENTINEL}, GATEWAY_FAILED_MESSAGE),
            ({}, GATEWAY_INVALID_REPLY_MESSAGE),
        ],
    )
    async def test_failure_maps_to_fixed_code_and_message(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        fields: dict,
        expected: str,
    ):
        req_q = FileQueueBackend(tmp_path, topic="req")
        resp_q = FileQueueBackend(tmp_path, topic="resp")
        client = GatewayClient(request_queue=req_q, response_queue=resp_q, timeout_seconds=3.0)
        try:
            fake = asyncio.create_task(
                _fake_gateway(
                    req_q,
                    resp_q,
                    reply=lambda request: ResponseEnvelope(
                        correlation_id=request.correlation_id, **fields
                    ),
                )
            )
            with caplog.at_level(logging.DEBUG), pytest.raises(LLMRouterError) as exc_info:
                await client.complete(settings=_settings(), messages=[{"role": "user", "content": "hi"}])
            await fake
        finally:
            await req_q.close()
            await resp_q.close()

        assert str(exc_info.value) == expected
        assert exc_info.value.code == LLM_UNAVAILABLE
        assert GATEWAY_TEXT_SENTINEL not in caplog.text


class _RecordingQueue:
    """Response queue stand-in that records each ``block_ms`` and yields nothing.

    With ``clock`` set, a blocking read advances that fake clock instead of sleeping.
    """

    def __init__(self, clock: list[float] | None = None) -> None:
        self.block_ms: list[int] = []
        self._clock = clock

    async def consume(self, *, batch_size: int, block_ms: int):
        self.block_ms.append(block_ms)
        if self._clock is not None:
            self._clock[0] += block_ms / 1000
            await asyncio.sleep(0)
        else:
            await asyncio.sleep(block_ms / 1000)
        return
        yield


class TestBlockingReadCap:
    """Each blocking read ends before the queue client's socket timeout."""

    def test_cap_is_below_the_redis_socket_timeout(self):
        from septum_queue import RedisStreamsQueueBackend

        socket_timeout = inspect.signature(RedisStreamsQueueBackend.from_url).parameters[
            "socket_timeout"
        ].default
        assert MAX_BLOCK_MS < socket_timeout * 1000
        assert MAX_BLOCK_MS < DEFAULT_CLIENT_TIMEOUT_SECONDS * 1000

    async def test_default_client_caps_the_first_read(self):
        queue = _RecordingQueue()
        client = GatewayClient(request_queue=None, response_queue=queue)
        deadline = time.monotonic() + DEFAULT_CLIENT_TIMEOUT_SECONDS
        waiter = asyncio.create_task(client._await_response("cid", deadline))
        await asyncio.sleep(0.05)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter

        assert queue.block_ms == [MAX_BLOCK_MS]

    async def test_reads_are_reissued_until_the_deadline(self, monkeypatch: pytest.MonkeyPatch):
        clock = [1000.0]
        monkeypatch.setattr(
            "septum_api.services.gateway_client.time",
            SimpleNamespace(monotonic=lambda: clock[0], time=time.time),
        )
        queue = _RecordingQueue(clock)
        client = GatewayClient(
            request_queue=None, response_queue=queue, timeout_seconds=0.35, max_block_ms=100
        )

        with pytest.raises(QueueTimeoutError):
            await client._await_response("cid", clock[0] + 0.35)

        assert queue.block_ms[:3] == [100, 100, 100]
        assert max(queue.block_ms) <= 100
        assert sum(queue.block_ms) <= 350


class TestMalformedReplies:
    """Unparseable replies and non-finite timestamps are acked and dropped."""

    @pytest.mark.parametrize(
        "payload",
        [
            {"text": GATEWAY_TEXT_SENTINEL},
            {"correlation_id": "stray", "text": GATEWAY_TEXT_SENTINEL, "created_at": "nan"},
            {"correlation_id": "stray", "text": GATEWAY_TEXT_SENTINEL, "created_at": float("inf")},
            {"correlation_id": "stray", "text": GATEWAY_TEXT_SENTINEL, "created_at": float("nan")},
            {"correlation_id": "stray", "text": GATEWAY_TEXT_SENTINEL, "created_at": float("-inf")},
            {"correlation_id": "stray", "text": {"nested": GATEWAY_TEXT_SENTINEL}},
            {"correlation_id": "stray", "created_at": "soon"},
        ],
    )
    async def test_malformed_reply_is_dropped(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture, payload: dict
    ):
        req_q = FileQueueBackend(tmp_path, topic="req")
        resp_q = FileQueueBackend(tmp_path, topic="resp")
        client = GatewayClient(request_queue=req_q, response_queue=resp_q, timeout_seconds=3.0)
        try:
            await resp_q.publish(payload)

            async def _delayed_gateway() -> None:
                await asyncio.sleep(0.2)
                await _fake_gateway(
                    req_q,
                    resp_q,
                    reply=lambda request: ResponseEnvelope(
                        correlation_id=request.correlation_id, text="ok"
                    ),
                )

            fake = asyncio.create_task(_delayed_gateway())
            with caplog.at_level(logging.DEBUG):
                assert await client.complete(
                    settings=_settings(), messages=[{"role": "user", "content": "hi"}]
                ) == "ok"
            await fake
            left = [message async for message in resp_q.consume(batch_size=16, block_ms=0)]
        finally:
            await req_q.close()
            await resp_q.close()

        assert left == []
        topic_dir = tmp_path / "resp"
        for unsettled in ("incoming", "processing", "dead-letter"):
            assert list((topic_dir / unsettled).iterdir()) == []
        assert len(list((topic_dir / "done").iterdir())) == 2
        assert "malformed gateway reply" in caplog.text
        assert GATEWAY_TEXT_SENTINEL not in caplog.text


class TestOverallRequestDeadline:
    """A queue call that never returns must not hold the chat request past the client timeout."""

    @pytest.mark.parametrize("hang_on", ["publish", "consume"])
    async def test_hung_queue_call_raises_queue_timeout(
        self, hang_on: str, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(gateway_module, "TIMEOUT_BACKSTOP_GRACE_SECONDS", 0.2)
        client = GatewayClient(
            request_queue=_HangingQueue(hang=hang_on == "publish"),
            response_queue=_HangingQueue(hang=hang_on == "consume"),
            timeout_seconds=0.05,
        )
        async with asyncio.timeout(5):
            with pytest.raises(QueueTimeoutError):
                await client.complete(
                    settings=_settings(),
                    messages=[{"role": "user", "content": "hi"}],
                )

    async def test_normal_timeout_does_not_cancel_the_in_flight_read(self):
        response_queue = _SlowReadQueue()
        client = GatewayClient(
            request_queue=_HangingQueue(hang=False),
            response_queue=response_queue,
            timeout_seconds=0.05,
        )
        async with asyncio.timeout(2):
            with pytest.raises(QueueTimeoutError):
                await client.complete(
                    settings=_settings(),
                    messages=[{"role": "user", "content": "hi"}],
                )
        assert response_queue.reads >= 1
        assert response_queue.cancelled is False

    async def test_queue_timeout_error_from_the_backend_is_not_relabelled(self):
        client = GatewayClient(
            request_queue=_HangingQueue(hang=False),
            response_queue=_RaisingReadQueue(),
            timeout_seconds=5,
        )
        with pytest.raises(TimeoutError) as exc_info:
            await client.complete(
                settings=_settings(),
                messages=[{"role": "user", "content": "hi"}],
            )
        assert not isinstance(exc_info.value, QueueTimeoutError)
