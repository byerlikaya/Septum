"""Factory for creating configured PIISanitizer instances."""
from __future__ import annotations

from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ..models.settings import AppSettings
from .ner_model_registry import get_shared_ner_registry
from .policy_composer import ComposedPolicy, PolicyComposer
from .sanitizer import PIISanitizer

DOCUMENT_SANITIZER_ENABLE_OLLAMA = False
"""Alias-layer setting of the sanitizer that masks stored document text.

The ingestion pipeline and the chat-time re-mask of outdated chunks both
build their sanitizer with it, so a chunk masked late runs the same layers a
reprocess would; only the re-mask drops custom rules' context-word gate (see
:func:`create_sanitizer`). The Ollama alias layer and pronoun coreference
stay off for both. The Ollama validation and contextual layers do not depend on
it: each follows its own stored setting (``use_ollama_validation_layer``,
``use_ollama_semantic_layer``) in both.
"""


async def create_sanitizer(
    db: AsyncSession,
    settings: AppSettings,
    enable_ollama: bool = False,
    policy: Optional[ComposedPolicy] = None,
    context_gate: bool = True,
) -> PIISanitizer:
    """Build a fully configured PIISanitizer from DB state.

    ``policy`` lets a caller that must record which policy it used compose
    it once and pass it in; otherwise the current policy is composed here.
    ``context_gate=False`` makes custom rules mask every match whatever
    their context words; :func:`.pii_gate.create_user_text_sanitizer` is the
    one place that asks for it, for chat queries, the chat re-mask of
    outdated chunks and everything gated by :mod:`.pii_gate` (custom
    ruleset names, edited approval chunks and chunk edits, column labels,
    retried pre-approved chunks). The gate stays on only where whole
    documents are masked: ingestion and reprocessing, and the custom rule
    test endpoint. Each call builds a new sanitizer, so the two modes never
    share recognizers.
    """
    if policy is None:
        policy = await PolicyComposer().compose(db, settings)
    ner_overrides = getattr(settings, "ner_model_overrides", None)
    ner_registry = get_shared_ner_registry(overrides=ner_overrides)
    return PIISanitizer(
        settings=settings,
        policy=policy,
        ner_registry=ner_registry,
        enable_ollama_layer=enable_ollama,
        context_gate=context_gate,
    )
