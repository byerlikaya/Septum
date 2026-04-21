"""Circuit breaker and provider health tracking for LLM providers.

Implements a simple circuit breaker pattern: after ``FAILURE_THRESHOLD``
consecutive failures within ``WINDOW_SECONDS``, the provider is marked
as "open" (unavailable) for ``COOLDOWN_SECONDS``.  After the cooldown
the breaker moves to "half-open" — a single probe request is allowed.
If the probe succeeds, the breaker closes; if it fails, it reopens.

State is held in a module-level singleton so it survives across requests
but resets on process restart (appropriate for container deployments).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict

logger = logging.getLogger(__name__)

FAILURE_THRESHOLD = 3
WINDOW_SECONDS = 120
COOLDOWN_SECONDS = 60


class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class _ProviderHealth:
    state: BreakerState = BreakerState.CLOSED
    failure_count: int = 0
    first_failure_at: float = 0.0
    opened_at: float = 0.0
    total_requests: int = 0
    total_failures: int = 0


_registry: Dict[str, _ProviderHealth] = {}


def _get_health(provider_key: str) -> _ProviderHealth:
    if provider_key not in _registry:
        _registry[provider_key] = _ProviderHealth()
    return _registry[provider_key]


def is_available(provider_key: str) -> bool:
    """Check if a provider is available (breaker closed or cooldown expired)."""
    health = _get_health(provider_key)

    if health.state == BreakerState.CLOSED:
        return True

    if health.state == BreakerState.OPEN:
        elapsed = time.monotonic() - health.opened_at
        if elapsed >= COOLDOWN_SECONDS:
            health.state = BreakerState.HALF_OPEN
            logger.info(
                "Circuit breaker half-open for %s after %ds cooldown",
                provider_key,
                int(elapsed),
            )
            return True
        return False

    # HALF_OPEN: allow one probe request
    return True


def record_success(provider_key: str) -> None:
    """Record a successful request — resets the breaker to closed."""
    health = _get_health(provider_key)
    health.total_requests += 1

    if health.state in (BreakerState.OPEN, BreakerState.HALF_OPEN):
        logger.info("Circuit breaker closed for %s after successful probe", provider_key)

    health.state = BreakerState.CLOSED
    health.failure_count = 0
    health.first_failure_at = 0.0


def record_failure(provider_key: str) -> None:
    """Record a failed request — may trip the breaker open."""
    now = time.monotonic()
    health = _get_health(provider_key)
    health.total_requests += 1
    health.total_failures += 1

    if health.state == BreakerState.HALF_OPEN:
        health.state = BreakerState.OPEN
        health.opened_at = now
        logger.warning(
            "Circuit breaker reopened for %s (half-open probe failed)", provider_key
        )
        return

    # Reset window if outside time window
    if health.first_failure_at and (now - health.first_failure_at) > WINDOW_SECONDS:
        health.failure_count = 0
        health.first_failure_at = 0.0

    if health.failure_count == 0:
        health.first_failure_at = now

    health.failure_count += 1

    if health.failure_count >= FAILURE_THRESHOLD:
        health.state = BreakerState.OPEN
        health.opened_at = now
        logger.warning(
            "Circuit breaker opened for %s after %d failures in %ds",
            provider_key,
            health.failure_count,
            int(now - health.first_failure_at),
        )


def get_provider_status(provider_key: str) -> dict:
    """Return status info for a provider (for health endpoint)."""
    health = _get_health(provider_key)
    return {
        "state": health.state.value,
        "failure_count": health.failure_count,
        "total_requests": health.total_requests,
        "total_failures": health.total_failures,
    }


def get_all_statuses() -> dict[str, dict]:
    """Return status info for all tracked providers."""
    return {key: get_provider_status(key) for key in _registry}


def reset(provider_key: str) -> None:
    """Manually reset a provider's circuit breaker."""
    _registry.pop(provider_key, None)
