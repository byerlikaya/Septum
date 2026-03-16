from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from app.models.settings import AppSettings
from app.routers.chat import router as chat_router


@pytest.fixture(name="app")
def app_fixture() -> FastAPI:
    """Create test app with chat router."""
    app = FastAPI()
    app.include_router(chat_router)
    return app


@pytest.mark.asyncio
async def test_desktop_assistant_send_disabled_returns_400(app: FastAPI) -> None:
    """Test that endpoint returns 400 when desktop assistant is disabled."""
    
    async def mock_load_settings(_: Any) -> AppSettings:
        return AppSettings(
            id=1,
            llm_provider="test",
            llm_model="test-model",
            ollama_base_url="http://localhost",
            ollama_chat_model="local-chat",
            ollama_deanon_model="local-deanon",
            deanon_enabled=True,
            deanon_strategy="simple",
            require_approval=False,
            show_json_output=False,
            use_presidio_layer=True,
            use_ner_layer=True,
            use_ollama_layer=False,
            chunk_size=800,
            chunk_overlap=200,
            top_k_retrieval=5,
            pdf_chunk_size=1200,
            audio_chunk_size=60,
            spreadsheet_chunk_size=200,
            whisper_model="base",
            default_audio_language=None,
            image_ocr_languages=["en"],
            ocr_provider="easyocr",
            ocr_provider_options=None,
            extract_embedded_images=True,
            recursive_email_attachments=True,
            default_active_regulations=["gdpr"],
            ner_model_overrides=None,
            desktop_assistant_enabled=False,  # Disabled
            desktop_assistant_default_target=None,
            desktop_assistant_chatgpt_new_chat_default=False,
        )
    
    with patch("app.routers.chat._load_settings", new=mock_load_settings):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/chat/desktop-assistant/send",
                json={"message": "hello", "target": "chatgpt"},
            )
    
    assert response.status_code == 400
    assert response.json()["detail"] == "Desktop assistant mode is disabled in settings."


@pytest.mark.asyncio
async def test_desktop_assistant_send_with_mocked_assistant() -> None:
    """Test that desktop assistant send works with proper mocking."""
    
    # Create mock assistant
    mock_assistant = MagicMock()
    mock_assistant.send_message = MagicMock()
    
    # Create mock settings
    mock_settings = AppSettings(
        id=1,
        llm_provider="test",
        llm_model="test-model",
        ollama_base_url="http://localhost",
        ollama_chat_model="local-chat",
        ollama_deanon_model="local-deanon",
        deanon_enabled=True,
        deanon_strategy="simple",
        require_approval=False,
        show_json_output=False,
        use_presidio_layer=True,
        use_ner_layer=True,
        use_ollama_layer=False,
        chunk_size=800,
        chunk_overlap=200,
        top_k_retrieval=5,
        pdf_chunk_size=1200,
        audio_chunk_size=60,
        spreadsheet_chunk_size=200,
        whisper_model="base",
        default_audio_language=None,
        image_ocr_languages=["en"],
        ocr_provider="easyocr",
        ocr_provider_options=None,
        extract_embedded_images=True,
        recursive_email_attachments=True,
        default_active_regulations=["gdpr"],
        ner_model_overrides=None,
        desktop_assistant_enabled=True,
        desktop_assistant_default_target="chatgpt",
        desktop_assistant_chatgpt_new_chat_default=False,
    )
    
    app = FastAPI()
    app.include_router(chat_router)
    
    async def mock_load_settings(_: Any) -> AppSettings:
        return mock_settings
    
    def mock_create_assistant(_: Any) -> MagicMock:
        return mock_assistant
    
    async def mock_to_thread(func: Any, *args: Any, **kwargs: Any) -> Any:
        return func(*args, **kwargs)
    
    mock_log_error = AsyncMock()
    
    with patch("app.routers.chat._load_settings", new=mock_load_settings), \
         patch("app.routers.chat.create_desktop_assistant", return_value=mock_assistant), \
         patch("app.routers.chat.asyncio.to_thread", new=mock_to_thread), \
         patch("app.routers.chat.log_backend_error", new=mock_log_error):
        
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/chat/desktop-assistant/send",
                json={"message": "test message", "target": "chatgpt", "open_new_chat": True},
            )
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    mock_assistant.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_desktop_assistant_send_with_rag() -> None:
    """Test that desktop assistant RAG mode calls build_rag_prompt."""
    
    mock_assistant = MagicMock()
    mock_assistant.send_message = MagicMock()
    
    mock_settings = AppSettings(
        id=1,
        llm_provider="test",
        llm_model="test-model",
        ollama_base_url="http://localhost",
        ollama_chat_model="local-chat",
        ollama_deanon_model="local-deanon",
        deanon_enabled=True,
        deanon_strategy="simple",
        require_approval=False,
        show_json_output=False,
        use_presidio_layer=True,
        use_ner_layer=True,
        use_ollama_layer=False,
        chunk_size=800,
        chunk_overlap=200,
        top_k_retrieval=5,
        pdf_chunk_size=1200,
        audio_chunk_size=60,
        spreadsheet_chunk_size=200,
        whisper_model="base",
        default_audio_language=None,
        image_ocr_languages=["en"],
        ocr_provider="easyocr",
        ocr_provider_options=None,
        extract_embedded_images=True,
        recursive_email_attachments=True,
        default_active_regulations=["gdpr"],
        ner_model_overrides=None,
        desktop_assistant_enabled=True,
        desktop_assistant_default_target="chatgpt",
        desktop_assistant_chatgpt_new_chat_default=False,
    )
    
    app = FastAPI()
    app.include_router(chat_router)
    
    async def mock_load_settings(_: Any) -> AppSettings:
        return mock_settings
    
    async def mock_build_rag_prompt(**kwargs: Any) -> str:
        return "RAG prompt with chunks"
    
    async def mock_to_thread(func: Any, *args: Any, **kwargs: Any) -> Any:
        return func(*args, **kwargs)
    
    mock_log_error = AsyncMock()
    
    with patch("app.routers.chat._load_settings", new=mock_load_settings), \
         patch("app.routers.chat.create_desktop_assistant", return_value=mock_assistant), \
         patch("app.routers.chat._build_rag_prompt_for_desktop", new=mock_build_rag_prompt), \
         patch("app.routers.chat.asyncio.to_thread", new=mock_to_thread), \
         patch("app.routers.chat.log_backend_error", new=mock_log_error):
        
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/chat/desktop-assistant/send",
                json={
                    "message": "test query",
                    "target": "claude",
                    "open_new_chat": False,
                    "use_rag": True,
                    "document_ids": [1, 2],
                    "top_k": 5,
                },
            )
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    # Verify that RAG prompt was used (not the original message)
    call_args = mock_assistant.send_message.call_args[0]
    assert call_args[0] == "RAG prompt with chunks"
