"""Tests for the LLM provider circuit breaker."""

from __future__ import annotations

import time

from septum_api.services.llm_providers.health import (
    COOLDOWN_SECONDS,
    FAILURE_THRESHOLD,
    _registry,
    get_provider_status,
    is_available,
    record_failure,
    record_success,
    reset,
)


def setup_function() -> None:
    """Clear registry before each test."""
    _registry.clear()


def test_new_provider_is_available() -> None:
    assert is_available("test:model") is True


def test_breaker_opens_after_threshold_failures() -> None:
    for _ in range(FAILURE_THRESHOLD):
        record_failure("test:model")
    assert is_available("test:model") is False
    assert get_provider_status("test:model")["state"] == "open"


def test_success_resets_breaker() -> None:
    for _ in range(FAILURE_THRESHOLD):
        record_failure("test:model")
    assert is_available("test:model") is False

    # Simulate cooldown expiry for half-open
    _registry["test:model"].opened_at = time.monotonic() - COOLDOWN_SECONDS - 1
    assert is_available("test:model") is True  # half-open

    record_success("test:model")
    assert is_available("test:model") is True
    assert get_provider_status("test:model")["state"] == "closed"


def test_half_open_failure_reopens_breaker() -> None:
    for _ in range(FAILURE_THRESHOLD):
        record_failure("test:model")

    _registry["test:model"].opened_at = time.monotonic() - COOLDOWN_SECONDS - 1
    assert is_available("test:model") is True  # half-open

    record_failure("test:model")
    assert get_provider_status("test:model")["state"] == "open"


def test_reset_clears_provider_state() -> None:
    for _ in range(FAILURE_THRESHOLD):
        record_failure("test:model")
    reset("test:model")
    assert is_available("test:model") is True


def test_failures_below_threshold_stay_closed() -> None:
    for _ in range(FAILURE_THRESHOLD - 1):
        record_failure("test:model")
    assert is_available("test:model") is True
    assert get_provider_status("test:model")["state"] == "closed"


def test_total_counters_track_all_requests() -> None:
    record_success("test:model")
    record_success("test:model")
    record_failure("test:model")

    status = get_provider_status("test:model")
    assert status["total_requests"] == 3
    assert status["total_failures"] == 1
