from __future__ import annotations

"""
Public facade for the septum-core detection + unmasking pipeline.

:class:`SeptumEngine` is the primary entry point for host applications
that want PII masking with minimal boilerplate. It wires together the
detector, the unmasker, an in-memory session registry, and the
recognizer registry for a list of regulation ids.

Example::

    from septum_core import SeptumEngine

    engine = SeptumEngine(regulations=["gdpr", "kvkk"])
    result = engine.mask("Contact: jane@example.com", language="en")
    # send result.masked_text to an external LLM
    restored = engine.unmask(response_text, result.session_id)

The engine never reaches the network on its own. Attach a
:class:`SemanticDetectionPort` via the ``semantic_port`` parameter when
a host wants the optional Ollama / LLM-assisted layers.
"""

import importlib
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .anonymization_map import AnonymizationMap
from .config import SeptumCoreConfig
from .detector import Detector
from .ner_model_registry import NERModelRegistry
from .ports import SemanticDetectionPort
from .recognizers.registry import RecognizerRegistry
from .regulations.composer import ComposedPolicy, PolicyComposer
from .spans import ResolvedSpan
from .unmasker import Unmasker


@dataclass
class _EngineRegulation:
    """In-memory stand-in for :class:`RegulationRulesetLike` rows.

    The engine composes a policy without touching the database, so it
    synthesises a minimal regulation record per requested id. The
    ``entity_types`` field is populated lazily from the recognizer
    pack's ``get_recognizers()`` output so the composer can still
    filter the detector pipeline down to the declared entity set.
    """

    id: str
    entity_types: List[str] = field(default_factory=list)


@dataclass
class MaskResult:
    """Return value of :meth:`SeptumEngine.mask`.

    ``session_id`` is the opaque handle that :meth:`SeptumEngine.unmask`
    uses to look up the anonymization map for a later de-anonymization
    call. ``masked_text`` is the placeholder-bearing copy safe to send
    to a remote LLM.
    """

    masked_text: str
    session_id: str
    entity_count: int
    entity_type_counts: Dict[str, int]
    detected_spans: List[ResolvedSpan] = field(default_factory=list)


def _entity_types_for_regulation(reg_id: str) -> List[str]:
    """Return the union of entity types declared by a regulation pack.

    Imports the pack's ``recognizers`` module and walks each
    recognizer's ``supported_entities`` attribute. Unknown regulation
    ids return an empty list; the policy composer will still produce
    a valid :class:`ComposedPolicy` without that pack's recognizers.
    """
    module_path = f"septum_core.recognizers.{reg_id}.recognizers"
    try:
        module = importlib.import_module(module_path)
    except ModuleNotFoundError:
        return []
    get_recognizers = getattr(module, "get_recognizers", None)
    if get_recognizers is None:
        return []
    try:
        recognizers = list(get_recognizers())
    except Exception:
        return []
    seen: set[str] = set()
    result: List[str] = []
    for recognizer in recognizers:
        for et in getattr(recognizer, "supported_entities", ()):
            if et not in seen:
                seen.add(et)
                result.append(et)
    return result


class SeptumEngine:
    """High-level facade: mask → send to cloud → unmask locally.

    Wraps :class:`Detector` and :class:`Unmasker` and maintains an
    in-memory ``session_id → AnonymizationMap`` registry so callers can
    round-trip a masked response through a remote model without ever
    exposing the original PII values off the host.
    """

    def __init__(
        self,
        regulations: Optional[List[str]] = None,
        *,
        config: Optional[SeptumCoreConfig] = None,
        ner_registry: Optional[NERModelRegistry] = None,
        semantic_port: Optional[SemanticDetectionPort] = None,
        policy: Optional[ComposedPolicy] = None,
    ) -> None:
        self._config = config or SeptumCoreConfig()
        if policy is None:
            policy = self._compose_policy(regulations or [])
        self._detector = Detector(
            config=self._config,
            ner_registry=ner_registry,
            policy=policy,
            semantic_port=semantic_port,
        )
        self._unmasker = Unmasker()
        self._sessions: Dict[str, AnonymizationMap] = {}

    @staticmethod
    def _compose_policy(regulation_ids: List[str]) -> ComposedPolicy:
        """Build a :class:`ComposedPolicy` from regulation ids alone.

        No custom recognizers or non-PII rules are attached because
        those only exist in the backend database; the resulting policy
        is enough for MCP tools and standalone scripts that just want
        the built-in regulation packs.
        """
        active_regs = [
            _EngineRegulation(id=rid, entity_types=_entity_types_for_regulation(rid))
            for rid in regulation_ids
        ]
        composer = PolicyComposer(recognizer_registry=RecognizerRegistry())
        return composer.compose_from_data(active_regs, [], [])

    def mask(self, text: str, language: str = "en") -> MaskResult:
        """Detect PII in ``text`` and return a placeholder-bearing copy."""
        session_id = uuid.uuid4().hex
        anon_map = AnonymizationMap(document_id=0, language=language)
        result = self._detector.sanitize(text=text, language=language, anon_map=anon_map)
        self._sessions[session_id] = anon_map
        return MaskResult(
            masked_text=result.sanitized_text,
            session_id=session_id,
            entity_count=result.entity_count,
            entity_type_counts=dict(result.entity_type_counts),
            detected_spans=list(result.detected_spans),
        )

    def unmask(self, text: str, session_id: str) -> str:
        """Restore original values in ``text`` using the recorded session map."""
        anon_map = self._sessions.get(session_id)
        if anon_map is None:
            return text
        return self._unmasker.unmask(text, anon_map)

    def release(self, session_id: str) -> None:
        """Drop a session's anonymization map from the in-memory registry."""
        self._sessions.pop(session_id, None)
