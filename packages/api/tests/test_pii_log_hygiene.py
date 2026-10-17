from __future__ import annotations

"""Logging must never carry raw document or LLM text — only lengths and labels."""

import logging

import pytest
from septum_api.services.ollama_client import extract_json_array
from septum_api.services.recognizers.base_recognizer import (
    RegexPatternConfig,
    ValidatedPatternRecognizer,
)

SECRET = "Jane Roe 12345678901"


def test_unparseable_ollama_response_logs_length_only(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        assert extract_json_array(f"not json at all: {SECRET}") == []

    assert caplog.records, "a warning is still expected"
    assert SECRET not in caplog.text
    assert "Jane" not in caplog.text


def test_failing_validator_logs_no_candidate_text(caplog: pytest.LogCaptureFixture) -> None:
    def _boom(value: str) -> bool:
        raise ValueError(f"bad checksum for {value}")

    recognizer = ValidatedPatternRecognizer(
        entity_type="TEST_ID",
        config=RegexPatternConfig(name="test_id", pattern=r"\d{11}"),
        algorithmic_validator=_boom,
    )

    with caplog.at_level(logging.DEBUG):
        assert recognizer.validate_result("12345678901") is False

    assert caplog.records, "the validator failure is still reported"
    assert "12345678901" not in caplog.text
    assert "ValueError" in caplog.text
