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

import threading
import time
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

DEFAULT_SESSION_TTL_SECONDS = 3600.0


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


from .recognizers import entity_types_for as _entity_types_for_regulation  # noqa: F401


class SeptumEngine:
    """High-level facade: mask → send to cloud → unmask locally.

    Wraps :class:`Detector` and :class:`Unmasker` and maintains an
    in-memory ``session_id → AnonymizationMap`` registry so callers can
    round-trip a masked response through a remote model without ever
    exposing the original PII values off the host.

    Sessions are evicted after ``session_ttl_seconds`` of inactivity so
    long-running processes (MCP stdio servers, background workers) do
    not accumulate unbounded anonymization maps. Pass ``0`` or a
    negative value to disable TTL eviction entirely.
    """

    def __init__(
        self,
        regulations: Optional[List[str]] = None,
        *,
        config: Optional[SeptumCoreConfig] = None,
        ner_registry: Optional[NERModelRegistry] = None,
        semantic_port: Optional[SemanticDetectionPort] = None,
        policy: Optional[ComposedPolicy] = None,
        session_ttl_seconds: float = DEFAULT_SESSION_TTL_SECONDS,
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
        self._session_expiry: Dict[str, float] = {}
        self._session_ttl_seconds = session_ttl_seconds
        # The MCP server and the FastAPI worker pool both call mask /
        # unmask / release from multiple threads. Without a lock the
        # eviction loop and concurrent registrations can race the two
        # session dicts, raising ``RuntimeError: dictionary changed
        # size during iteration`` or — worse — leaking an entry whose
        # expiry was already deleted (so the AnonymizationMap with raw
        # PII never re-evicts).
        self._sessions_lock = threading.RLock()

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
        self._evict_expired_sessions()
        session_id = uuid.uuid4().hex
        anon_map = AnonymizationMap(document_id=0, language=language)
        result = self._detector.sanitize(text=text, language=language, anon_map=anon_map)
        with self._sessions_lock:
            self._sessions[session_id] = anon_map
            self._touch_session_locked(session_id)
        return MaskResult(
            masked_text=result.sanitized_text,
            session_id=session_id,
            entity_count=result.entity_count,
            entity_type_counts=dict(result.entity_type_counts),
            detected_spans=list(result.detected_spans),
        )

    def unmask(self, text: str, session_id: str) -> str:
        """Restore original values in ``text`` using the recorded session map."""
        anon_map = self._get_live_session(session_id)
        if anon_map is None:
            return text
        with self._sessions_lock:
            self._touch_session_locked(session_id)
        return self._unmasker.unmask(text, anon_map)

    def get_session_map(self, session_id: str) -> Optional[Dict[str, str]]:
        """Return the ``{original: placeholder}`` map for ``session_id``.

        Returns ``None`` when the session does not exist or has expired.
        The returned dict is a shallow copy so callers cannot mutate the
        engine's internal state. Intended for debugging tools (for
        example the MCP ``get_session_map`` tool) — do not ship this
        value off the host.
        """
        anon_map = self._get_live_session(session_id)
        if anon_map is None:
            return None
        return dict(anon_map.entity_map)

    def release(self, session_id: str) -> None:
        """Drop a session's anonymization map from the in-memory registry."""
        with self._sessions_lock:
            self._sessions.pop(session_id, None)
            self._session_expiry.pop(session_id, None)

    def active_session_count(self) -> int:
        """Return the number of non-expired sessions currently held."""
        self._evict_expired_sessions()
        with self._sessions_lock:
            return len(self._sessions)

    def _touch_session_locked(self, session_id: str) -> None:
        """Update expiry. Caller must hold ``self._sessions_lock``."""
        if self._session_ttl_seconds <= 0:
            return
        self._session_expiry[session_id] = time.monotonic() + self._session_ttl_seconds

    def _get_live_session(self, session_id: str) -> Optional[AnonymizationMap]:
        with self._sessions_lock:
            anon_map = self._sessions.get(session_id)
            if anon_map is None:
                return None
            if self._session_ttl_seconds > 0:
                expiry = self._session_expiry.get(session_id, 0.0)
                if expiry and time.monotonic() > expiry:
                    self._sessions.pop(session_id, None)
                    self._session_expiry.pop(session_id, None)
                    return None
        return anon_map

    def _evict_expired_sessions(self) -> None:
        if self._session_ttl_seconds <= 0:
            return
        with self._sessions_lock:
            if not self._session_expiry:
                return
            now = time.monotonic()
            expired = [
                sid for sid, exp in self._session_expiry.items() if now > exp
            ]
            for sid in expired:
                self._sessions.pop(sid, None)
                self._session_expiry.pop(sid, None)
