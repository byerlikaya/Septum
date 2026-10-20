"""Factory for creating configured PIISanitizer instances."""
from __future__ import annotations

from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ..models.settings import AppSettings
from .ner_model_registry import get_shared_ner_registry
from .policy_composer import ComposedPolicy, PolicyComposer
from .sanitizer import PIISanitizer

DOCUMENT_SANITIZER_ENABLE_OLLAMA = False
"""Semantic-detection setting of the sanitizer that masks stored document text.

The ingestion pipeline and the chat-time re-mask of outdated chunks both
build their sanitizer with it, so a chunk masked late is masked exactly as a
reprocess would mask it. The Ollama semantic detection layer stays off for
both; the Ollama validation and contextual layers follow the stored settings
(``use_ollama_validation_layer``, ``use_ollama_semantic_layer``) in both.
"""


async def create_sanitizer(
    db: AsyncSession,
    settings: AppSettings,
    enable_ollama: bool = False,
    policy: Optional[ComposedPolicy] = None,
) -> PIISanitizer:
    """Build a fully configured PIISanitizer from DB state.

    ``policy`` lets a caller that must record which policy it used compose
    it once and pass it in; otherwise the current policy is composed here.
    """
    if policy is None:
        policy = await PolicyComposer().compose(db)
    ner_overrides = getattr(settings, "ner_model_overrides", None)
    ner_registry = get_shared_ner_registry(overrides=ner_overrides)
    return PIISanitizer(
        settings=settings,
        policy=policy,
        ner_registry=ner_registry,
        enable_ollama_layer=enable_ollama,
    )
