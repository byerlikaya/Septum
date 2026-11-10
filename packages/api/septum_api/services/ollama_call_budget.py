"""Per-request budget of local-model detection calls.

A chat turn, ``/analyze_query`` and a custom rule test install a budget;
every detection request, each retry included, is reserved against it before
it is sent. Without one (document processing, MCP) reserving is free. The
budget is a :class:`~contextvars.ContextVar`, so it follows the request into
``asyncio.to_thread`` workers and the runner's pool, which run in a copy of
the caller's context.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Optional

from ..errors import OllamaCallBudgetExceededError
from ..request_limits import MAX_OLLAMA_CALLS_PER_TURN
from ..utils.context_vars import scoped_value


class OllamaCallBudget:
    """A thread-safe count of the detection calls one request may still send."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0
        self._lock = threading.Lock()

    def reserve(self, count: int) -> None:
        """Take ``count`` calls, or raise without taking any when fewer remain."""
        if count <= 0:
            return
        with self._lock:
            if self.used + count > self.limit:
                raise OllamaCallBudgetExceededError(
                    f"Local model call budget exceeded ({self.used} used, "
                    f"{count} more requested, limit {self.limit})."
                )
            self.used += count


_current_budget: ContextVar[Optional[OllamaCallBudget]] = ContextVar(
    "ollama_call_budget", default=None
)


@contextmanager
def ollama_call_budget(limit: int = MAX_OLLAMA_CALLS_PER_TURN) -> Iterator[OllamaCallBudget]:
    """Install a fresh budget of ``limit`` calls for the code run inside the block."""
    budget = OllamaCallBudget(limit)
    with scoped_value(_current_budget, budget):
        yield budget


def reserve_ollama_calls(count: int) -> None:
    """Reserve ``count`` detection calls against the installed budget; free when none is installed."""
    budget = _current_budget.get()
    if budget is not None:
        budget.reserve(count)


__all__ = ["OllamaCallBudget", "ollama_call_budget", "reserve_ollama_calls"]
