"""Helpers for request-scoped :class:`~contextvars.ContextVar` values."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, TypeVar

T = TypeVar("T")


@contextmanager
def scoped_value(var: ContextVar[T], value: T) -> Iterator[None]:
    """Set ``var`` to ``value`` for the block, then put the previous value back.

    The previous value is restored with ``set`` rather than a token reset, so
    closing an async generator from another context cannot fail.
    """
    previous = var.get()
    var.set(value)
    try:
        yield
    finally:
        var.set(previous)
