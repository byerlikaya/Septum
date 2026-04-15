from __future__ import annotations

"""Abstract protocols used to inject optional network-dependent layers.

The core package must never import any network client directly. Features
that rely on a locally hosted or remote LLM (for example Ollama-based
semantic validation) are modelled as :class:`SemanticDetectionPort` so
that callers — typically the backend, MCP server or tests — can plug
in their own adapter without pulling ``httpx``/``requests`` into core.

A null implementation (:class:`NullSemanticDetectionPort`) is provided
for the common case where no semantic layer is wired up.
"""

from typing import List, Protocol, runtime_checkable

from .anonymization_map import AnonymizationMap
from .spans import DetectedSpan


@runtime_checkable
class SemanticDetectionPort(Protocol):
    """Adapter contract for an optional semantic / LLM detection layer.

    All methods are synchronous so that the detector can call them from
    its own blocking pipeline without having to thread an event loop
    through every layer. Network concerns (timeouts, retries, model
    selection) live entirely inside the adapter.
    """

    def is_enabled(self) -> bool:
        """Return True when the adapter is configured and ready to use."""

    def validate_candidates(
        self,
        *,
        text: str,
        candidate_spans: List[DetectedSpan],
        language: str,
        regulation_context: str,
    ) -> List[DetectedSpan]:
        """Filter a set of detected spans down to the real PII matches."""

    def detect_aliases(self, *, normalized_text: str) -> List[DetectedSpan]:
        """Return alias / nickname spans that previous layers may have missed."""

    def detect_semantic(
        self,
        *,
        normalized_text: str,
        entity_types: List[str],
    ) -> List[DetectedSpan]:
        """Return spans for contextual/semantic entity types (DIAGNOSIS, …)."""

    def resolve_coreference(
        self,
        *,
        normalized_text: str,
        anon_map: AnonymizationMap,
        language: str,
    ) -> int:
        """Add pronoun / coreference tokens to ``anon_map``; return the count."""


class NullSemanticDetectionPort:
    """Inert implementation used when no semantic adapter is attached.

    Every method returns an empty result so that the detector's main
    pipeline can call through unconditionally without additional
    ``if port is None`` guards.
    """

    def is_enabled(self) -> bool:
        return False

    def validate_candidates(
        self,
        *,
        text: str,
        candidate_spans: List[DetectedSpan],
        language: str,
        regulation_context: str,
    ) -> List[DetectedSpan]:
        return list(candidate_spans)

    def detect_aliases(self, *, normalized_text: str) -> List[DetectedSpan]:
        return []

    def detect_semantic(
        self,
        *,
        normalized_text: str,
        entity_types: List[str],
    ) -> List[DetectedSpan]:
        return []

    def resolve_coreference(
        self,
        *,
        normalized_text: str,
        anon_map: AnonymizationMap,
        language: str,
    ) -> int:
        return 0
