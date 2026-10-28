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


class _CapturingSession:
    def __init__(self) -> None:
        self.rows: list = []

    def add(self, row) -> None:
        self.rows.append(row)

    async def commit(self) -> None:
        return None


async def test_error_log_row_keeps_class_and_frames_not_message() -> None:
    from septum_api.services.error_logger import log_backend_error

    def _inner() -> None:
        raise KeyError(f"lookup of {SECRET}")

    def _outer() -> None:
        try:
            _inner()
        except KeyError as exc:
            raise RuntimeError(f"wrapped {SECRET}") from exc

    try:
        _outer()
    except RuntimeError as exc:
        caught = exc

    db = _CapturingSession()
    await log_backend_error(db, None, caught, status_code=500)

    row = db.rows[0]
    assert row.message == "RuntimeError"
    assert row.status_code == 500
    assert row.exception_type == "RuntimeError"
    assert SECRET not in row.stack_trace
    assert "Jane" not in row.stack_trace
    assert " in _outer" in row.stack_trace
    assert "KeyError" in row.stack_trace and " in _inner" in row.stack_trace


async def test_error_log_keeps_authored_content_free_message() -> None:
    from septum_api.errors import ContentFreeError
    from septum_api.services.error_logger import log_backend_error

    class _Authored(ContentFreeError):
        pass

    db = _CapturingSession()
    await log_backend_error(db, None, _Authored("Could not remove FAISS index for document 7"))

    assert db.rows[0].message == "Could not remove FAISS index for document 7"
    assert db.rows[0].exception_type == "_Authored"


async def test_ollama_transport_error_logs_class_only(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx
    from septum_api.services import ollama_client

    class _FailingClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc) -> None:
            return None

        async def post(self, *args, **kwargs):
            raise httpx.ConnectError(f"refused while sending {SECRET}")

    monkeypatch.setattr(ollama_client.httpx, "AsyncClient", _FailingClient)
    with caplog.at_level(logging.DEBUG):
        out = await ollama_client.call_ollama_async(SECRET, base_url="http://localhost:11434")

    assert out == ""
    assert SECRET not in caplog.text
    assert "ConnectError" in caplog.text


async def test_redis_failure_logs_class_only(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from septum_api.services import redis_client

    class _FailingRedis:
        async def get(self, key):
            raise ConnectionError(f"redis://:{SECRET}@cache:6379 unreachable")

    def _get_redis():
        return _FailingRedis()

    monkeypatch.setattr(redis_client, "get_redis", _get_redis)
    with caplog.at_level(logging.DEBUG):
        assert await redis_client.redis_get("session:42") is None

    assert SECRET not in caplog.text
    assert "ConnectionError" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)
