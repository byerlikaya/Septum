"""Semantic-layer failures must not write document text into the logs."""

from __future__ import annotations

import logging

import pytest
from septum_core import SeptumCoreConfig, SeptumEngine
from septum_core import detector as detector_module

SENTINEL = "Sentinel Docutext Q7X9"


class _LeakyFailingPort:
    """A semantic adapter whose errors echo the text it was given."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def is_enabled(self) -> bool:
        return True

    def _fail(self, name: str, text: str):
        self.calls.append(name)
        raise RuntimeError(f"model rejected input: {text}")

    def validate_candidates(self, *, text, candidate_spans, language, regulation_context):
        self._fail("validate_candidates", text)

    def detect_aliases(self, *, normalized_text):
        self._fail("detect_aliases", normalized_text)

    def detect_semantic(self, *, normalized_text, entity_types):
        self._fail("detect_semantic", normalized_text)

    def resolve_coreference(self, *, normalized_text, anon_map, language):
        self._fail("resolve_coreference", normalized_text)


def _engine(port: _LeakyFailingPort, *, contextual: bool) -> SeptumEngine:
    config = SeptumCoreConfig(
        use_presidio_layer=True,
        use_ner_layer=False,
        use_semantic_validation=True,
        use_semantic_detection=True,
        use_semantic_contextual_detection=contextual,
    )
    return SeptumEngine(regulations=["gdpr", "hipaa"], config=config, semantic_port=port)


def _own_messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name.startswith("septum_core")]


TEXT = (
    f"Patient note: {SENTINEL} wrote to jane@example.com about the follow-up visit "
    "and asked for the lab results to be sent before Friday."
)


def test_semantic_layer_errors_log_only_the_exception_class(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(detector_module, "_SEMANTIC_VALIDATION_PASSTHROUGH_TYPES", frozenset())
    port = _LeakyFailingPort()

    with caplog.at_level(logging.DEBUG, logger="septum_core"):
        result = _engine(port, contextual=False).mask(TEXT, language="en")
    own = _own_messages(caplog)

    assert "jane@example.com" not in result.masked_text
    assert set(port.calls) == {"validate_candidates", "detect_aliases", "resolve_coreference"}
    assert not any(SENTINEL in m or "jane@example.com" in m for m in own)
    failure_lines = [m for m in own if "fail" in m.lower()]
    assert len(failure_lines) == 3
    assert all(line.endswith("RuntimeError") for line in failure_lines)


def test_a_contextual_layer_error_is_raised_and_logs_only_the_exception_class(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(detector_module, "_SEMANTIC_VALIDATION_PASSTHROUGH_TYPES", frozenset())
    port = _LeakyFailingPort()

    with caplog.at_level(logging.DEBUG, logger="septum_core"):
        with pytest.raises(RuntimeError):
            _engine(port, contextual=True).mask(TEXT, language="en")
    own = _own_messages(caplog)

    assert "detect_semantic" in port.calls
    assert "resolve_coreference" not in port.calls
    assert not any(SENTINEL in m or "jane@example.com" in m for m in own)
    contextual_lines = [m for m in own if "contextual" in m.lower() and "fail" in m.lower()]
    assert contextual_lines == ["Semantic contextual layer failed: RuntimeError"]
