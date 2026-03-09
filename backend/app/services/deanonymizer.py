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
* ``ollama`` – first applies the simple strategy, then passes the resulting
  text to a local Ollama model for light post-processing (for example,
  improving fluency) via the HTTP API configured in
  ``AppSettings.ollama_base_url`` and ``AppSettings.ollama_deanon_model``.
"""

from typing import Any
import asyncio
import httpx

from .anonymization_map import AnonymizationMap
from ..models.settings import AppSettings


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
        if strategy == "ollama":
            return await self._ollama(text, anon_map)

        # Unknown strategies fall back to simple replacement for safety.
        return self._simple(text, anon_map)

    def _simple(self, text: str, anon_map: AnonymizationMap) -> str:
        """Simple placeholder replacement using the anonymization map."""
        if not anon_map.entity_map:
            return text

        result = text
        # entity_map is original → placeholder; we invert it on the fly.
        for original, placeholder in anon_map.entity_map.items():
            if not placeholder:
                continue
            if placeholder in result:
                result = result.replace(placeholder, original)
        return result

    async def _ollama(self, text: str, anon_map: AnonymizationMap) -> str:
        """De-anonymize and lightly post-process text using a local Ollama model.

        This strategy first performs local placeholder replacement and then sends
        the fully de-anonymized text to the Ollama HTTP API running on the local
        machine. No anonymization map or additional metadata is transmitted.
        """
        base_text = self._simple(text, anon_map)
        if not base_text:
            return base_text

        url = f"{self._settings.ollama_base_url.rstrip('/')}/api/generate"
        payload: dict[str, Any] = {
            "model": self._settings.ollama_deanon_model,
            "prompt": (
                "You are a post-processor for de-anonymized chat responses. "
                "Improve readability and fix minor grammatical issues, but do not "
                "change the factual content of the answer.\n\n"
                "Response:\n"
                f"{base_text}"
            ),
            "stream": False,
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError:
            # If the local model is unavailable, fall back to the simple strategy.
            return base_text

        generated = data.get("response")
        if isinstance(generated, str) and generated.strip():
            return generated
        return base_text


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
        # Convert placeholder→original into the original→placeholder mapping used
        # by :class:`AnonymizationMap` and :class:`Deanonymizer`.
        for placeholder, original in entity_map.items():
            if not placeholder or not original:
                continue
            amap.entity_map[original] = placeholder

        # Delegate to the async implementation using a temporary event loop.
        return asyncio.run(self._inner.deanonymize(text, anon_map=amap))


