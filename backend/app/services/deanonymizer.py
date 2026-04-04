from __future__ import annotations

"""
Local de-anonymization strategies for Septum.

This module is responsible for turning LLM responses that still contain
placeholders (for example ``[PERSON_NAME_1]``) back into human-readable
answers using only local data. It never sends the anonymization map or any
other sensitive metadata to remote cloud services.

Two strategies are supported via ``AppSettings.deanon_strategy``:

* ``simple`` – direct string replacement of placeholders using the in-memory
  :class:`AnonymizationMap`.
* ``ollama`` – sends the masked LLM response and a placeholder→value map to
  local Ollama; the model returns the final text with placeholders replaced.
  All processing is local (OLLAMA_BASE_URL).
"""

import asyncio
import json
import re

from ..models.settings import AppSettings
from .anonymization_map import AnonymizationMap
from .ollama_client import call_ollama_async, use_ollama_enabled
from .prompts import PromptCatalog


class Deanonymizer:
    """Apply de-anonymization to LLM outputs according to application settings."""

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings

    async def deanonymize(self, text: str, anon_map: AnonymizationMap) -> str:
        """Return a de-anonymized version of ``text`` using the configured strategy.

        If de-anonymization is disabled in :class:`AppSettings`, the input text is
        returned unchanged.
        """
        if not self._settings.deanon_enabled or not text:
            return text

        strategy = (self._settings.deanon_strategy or "simple").strip().lower()
        if strategy == "simple":
            return self._simple(text, anon_map)
        if strategy == "ollama" and use_ollama_enabled():
            return await self._ollama(text, anon_map)

        return self._simple(text, anon_map)

    _PLACEHOLDER_SHORT_ALIASES = (
        (re.compile(r"^\[PERSON_NAME_(\d+)\]$"), "PERSON"),
        (re.compile(r"^\[ORGANIZATION_NAME_(\d+)\]$"), "ORGANIZATION"),
    )

    def _simple(self, text: str, anon_map: AnonymizationMap) -> str:
        """Simple placeholder replacement using the anonymization map."""
        if not anon_map.entity_map:
            return text

        result = text
        for original, placeholder in anon_map.entity_map.items():
            if not placeholder:
                continue
            if placeholder in result:
                result = result.replace(placeholder, original)
            for pattern, short_prefix in self._PLACEHOLDER_SHORT_ALIASES:
                match = pattern.match(placeholder)
                if match:
                    short_form = f"[{short_prefix}_{match.group(1)}]"
                    if short_form in result:
                        result = result.replace(short_form, original)
                    break
        return result

    async def _ollama(self, text: str, anon_map: AnonymizationMap) -> str:
        """De-anonymize using local Ollama: send placeholder→value map and masked text.

        Ollama returns the final text with placeholders replaced. All processing
        is local; no original values are sent to the cloud.
        """
        placeholder_to_original: dict[str, str] = {}
        for original, placeholder in anon_map.entity_map.items():
            if placeholder and original:
                placeholder_to_original[placeholder] = original
        if not placeholder_to_original:
            return self._simple(text, anon_map)

        entity_map_json = json.dumps(placeholder_to_original, ensure_ascii=False)
        prompt = PromptCatalog.deanonymizer_ollama(entity_map_json, text)
        result = await call_ollama_async(
            prompt=prompt,
            base_url=self._settings.ollama_base_url,
            model=self._settings.ollama_deanon_model,
        )
        if result and result.strip():
            return result.strip()
        return self._simple(text, anon_map)


class DeAnonymizer:
    """Synchronous convenience wrapper around :class:`Deanonymizer`.

    This wrapper exists primarily for small local scripts and CLI usage where
    an async entrypoint and the full :class:`AppSettings` wiring would be
    cumbersome. It expects a simple ``entity_map`` of ``{placeholder: original}``
    and delegates to the async :class:`Deanonymizer` under the hood.

    The main FastAPI application should use :class:`Deanonymizer` directly with
    :class:`AnonymizationMap` instances.
    """

    def __init__(self, strategy: str = "simple") -> None:
        from ..config import get_settings

        settings = get_settings()
        settings.deanon_strategy = strategy
        self._inner = Deanonymizer(settings=settings)

    def deanonymize(self, text: str, entity_map: dict[str, str]) -> str:
        """Synchronously de-anonymize ``text`` using a placeholder→original map."""
        if not text or not entity_map:
            return text

        amap = AnonymizationMap(document_id=0, language="en")
        for placeholder, original in entity_map.items():
            if not placeholder or not original:
                continue
            amap.entity_map[original] = placeholder

        return asyncio.run(self._inner.deanonymize(text, anon_map=amap))


