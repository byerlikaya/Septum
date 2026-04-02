"""Factory for creating configured PIISanitizer instances."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from .policy_composer import PolicyComposer
from .ner_model_registry import NERModelRegistry
from .sanitizer import PIISanitizer
from ..models.settings import AppSettings


async def create_sanitizer(
    db: AsyncSession,
    settings: AppSettings,
    enable_ollama: bool = False,
) -> PIISanitizer:
    """Build a fully configured PIISanitizer from DB state."""
    policy = await PolicyComposer().compose(db)
    ner_overrides = getattr(settings, "ner_model_overrides", None)
    ner_registry = NERModelRegistry(_overrides=ner_overrides) if ner_overrides else NERModelRegistry()
    return PIISanitizer(
        settings=settings,
        policy=policy,
        ner_registry=ner_registry,
        enable_ollama_layer=enable_ollama,
    )
