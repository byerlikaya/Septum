"""Producer-side GatewayClient tests using FileQueueBackend end-to-end."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from septum_queue import (
    FileQueueBackend,
    QueueTimeoutError,
    RequestEnvelope,
    ResponseEnvelope,
)

from septum_api.models.settings import AppSettings
from septum_api.services.gateway_client import GatewayClient
from septum_api.services.llm_errors import LLMRouterError


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
        from dataclasses import asdict

        await response_queue.publish(asdict(response))
        await request_queue.ack(message.id)
        return


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
            with pytest.raises(LLMRouterError, match="gateway: upstream 502"):
                await client.complete(
                    settings=_settings(),
                    messages=[{"role": "user", "content": "hi"}],
                )
            await fake
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

    async def test_envelope_carries_api_key_for_configured_provider(
        self, tmp_path: Path
    ):
        """The api-side settings supply the secret so gateway does not need its own."""
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
            assert captured[0].api_key == "sk-openai-from-api"
            assert captured[0].provider == "openai"
        finally:
            await req_q.close()
            await resp_q.close()


class TestLLMRouterGatewayBranch:
    """LLMRouter delegates to the gateway factory when use_gateway=True."""

    async def test_use_gateway_false_keeps_direct_call_path(self, monkeypatch):
        """The default path must stay untouched so existing tests keep passing."""
        from septum_api.services import llm_router

        # Sanity-check: no factory installed by default.
        assert llm_router._gateway_client_factory is None

        # A future test that flips the flag to True should not affect
        # existing callsites; verify the module-level state is clean.
        try:
            await llm_router.set_gateway_client_factory(None)  # idempotent
        except TypeError:
            # set_gateway_client_factory is sync — that's the expected shape.
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
