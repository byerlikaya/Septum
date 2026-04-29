from __future__ import annotations

"""
Backend de-anonymizer — deterministic placeholder → original replacement.

Delegates to :class:`septum_core.unmasker.Unmasker` for the actual
substitution. The previous Ollama-based strategy was removed: the cloud
LLM preserves placeholder tokens verbatim, so the deterministic regex
path is faster, predictable, and free of LLM hallucination risk.
"""

import asyncio

from septum_core.anonymization_map import AnonymizationMap
from septum_core.unmasker import Unmasker

from ..models.settings import AppSettings


class Deanonymizer:
    """Apply de-anonymization to LLM outputs."""

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings
        self._unmasker = Unmasker()

    async def deanonymize(self, text: str, anon_map: AnonymizationMap) -> str:
        """Return a de-anonymized version of ``text``.

        If de-anonymization is disabled in :class:`AppSettings`, the input
        text is returned unchanged.
        """
        if not self._settings.deanon_enabled or not text:
            return text
        return self._unmasker.unmask(text, anon_map)


class DeAnonymizer:
    """Synchronous convenience wrapper around :class:`Deanonymizer`.

    Exists for local scripts and CLI usage where async + full
    :class:`AppSettings` wiring would be cumbersome. Expects a simple
    ``entity_map`` of ``{placeholder: original}``.
    """

    def __init__(self) -> None:
        from ..config import get_settings

        self._inner = Deanonymizer(settings=get_settings())

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


__all__ = ["Deanonymizer", "DeAnonymizer"]
