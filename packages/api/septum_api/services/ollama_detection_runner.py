"""Runs a batch of local-model detection calls: cached, budgeted, bounded and retried.

Replies are cached in memory per user, prompt fingerprint, server and model, and
only inside :func:`detection_cache_scope`. The misses are reserved against the
request's call budget before any is sent, then sent at most
:data:`DETECTION_PARALLELISM` at a time, a retryable failure once more. The first
failure raises the caller's refusal and no partial result is returned. An erasure
(:func:`clear_detection_cache`) empties the cache here and replaces the shared
epoch, so every other worker treats its older entries as misses.
"""

from __future__ import annotations

import contextvars
import copy
import hashlib
import json
import logging
import os
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import ExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import (
    AsyncIterator,
    Callable,
    Generic,
    Iterator,
    List,
    Optional,
    Sequence,
    TypeVar,
    cast,
)

from ..utils.context_vars import scoped_value
from .document_anon_store import bump_erasure_epoch, read_erasure_epoch
from .ollama_call_budget import ollama_call_budget, reserve_ollama_calls
from .ollama_client import OllamaCallError, ollama_target
from .prompts import DATA_NONCE_SENTINEL, DetectionPrompt, new_data_nonce

logger = logging.getLogger(__name__)

T = TypeVar("T")

_PARALLELISM_ENV = "OLLAMA_NUM_PARALLEL"
_DEFAULT_PARALLELISM = 1
_MAX_PARALLELISM = 16


def _parallelism() -> int:
    """Calls in flight at once: ``OLLAMA_NUM_PARALLEL`` when set to 1-16, else 1.

    More than the server serves at once only queues there, and the queue time
    counts against each call's timeout.
    """
    raw = os.getenv(_PARALLELISM_ENV)
    if raw is None or not raw.strip():
        return _DEFAULT_PARALLELISM
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if not 1 <= value <= _MAX_PARALLELISM:
        logger.warning(
            "Ignoring %s: expected an integer from 1 to %d; using %d",
            _PARALLELISM_ENV,
            _MAX_PARALLELISM,
            _DEFAULT_PARALLELISM,
        )
        return _DEFAULT_PARALLELISM
    return value


DETECTION_PARALLELISM = _parallelism()
"""Most detection calls this process sends at once, across all requests."""

_ATTEMPTS = 2
RETRY_BACKOFF_SECONDS = 0.5
"""Wait before the second attempt of a call whose first failure may be cured by a retry."""

CACHE_TTL_SECONDS = 300.0
CACHE_MAX_ENTRIES = 1024

UNPARSABLE_REPLY = "unparsable_reply"
CANCELLED_CALL = "cancelled"

_Target = tuple[str, str, str]
"""``(prompt fingerprint, server, model)`` of a call."""
_Key = tuple[str, str, str, str]
"""``(cache owner, prompt fingerprint, server, model)`` of a cache entry."""


@dataclass(frozen=True)
class DetectionCall(Generic[T]):
    """One detection request: its prompt for a nonce, its reply parser and its target.

    ``parse`` returns ``None`` (or raises) for a reply that cannot be trusted:
    retried, then refused. Any other value is the call's result and is cached.
    """

    prompt: Callable[[str], DetectionPrompt]
    parse: Callable[[str], Optional[T]]
    base_url: Optional[str]
    model: Optional[str]


@dataclass(frozen=True)
class _CacheScope:
    """Whose cache entries a request may read and write, and under which erasure epoch."""

    owner: str
    epoch: str


_cache_scope: contextvars.ContextVar[Optional[_CacheScope]] = contextvars.ContextVar(
    "ollama_detection_cache_scope", default=None
)


@contextmanager
def detection_cache_scope(owner_id: Optional[int], epoch: Optional[str]) -> Iterator[None]:
    """Let the detection calls in the block share cached replies of ``owner_id`` only.

    ``owner_id`` is the user the request runs for (the document owner when a
    document is processed), ``epoch`` the erasure epoch read when it started.
    Without either, the block uses no cache, so a request with no user never
    shares a reply with anyone.
    """
    scope = None if owner_id is None or epoch is None else _CacheScope(f"user:{owner_id}", epoch)
    with scoped_value(_cache_scope, scope):
        yield


@asynccontextmanager
async def local_model_scope(user_id: Optional[int], *, budgeted: bool) -> AsyncIterator[None]:
    """Run a request's local-model calls for ``user_id``: its cache scope and, if ``budgeted``, a call budget.

    Reads the erasure epoch first. Chat turns, ``/analyze_query`` and a custom
    rule test are budgeted; document processing is not.
    """
    epoch = await read_erasure_epoch()
    with ExitStack() as stack:
        if budgeted:
            stack.enter_context(ollama_call_budget())
        stack.enter_context(detection_cache_scope(user_id, epoch))
        yield


class _ReplyCache:
    """A thread-safe LRU of parsed replies that expire after :data:`CACHE_TTL_SECONDS`.

    Every entry belongs to the last erasure epoch seen: a read or write under
    another epoch first drops all entries, and each write drops the expired
    ones, so nothing derived before an erasure outlives it. :meth:`clear` also
    advances a generation, and a value whose calls started under an older
    generation is not stored.
    """

    def __init__(self) -> None:
        self._entries: OrderedDict[_Key, tuple[float, object]] = OrderedDict()
        self._generation = 0
        self._epoch: Optional[str] = None
        self._lock = threading.Lock()

    def _observe(self, epoch: str) -> None:
        """Drop every entry when ``epoch`` is not the last one seen; hold the lock."""
        if epoch != self._epoch:
            self._epoch = epoch
            self._entries.clear()

    def generation(self) -> int:
        """The current generation; read before a batch's calls are sent."""
        with self._lock:
            return self._generation

    def get(self, key: _Key, epoch: str) -> tuple[bool, object]:
        """``(True, a copy of the value)`` for a live entry under ``epoch``, else ``(False, None)``."""
        with self._lock:
            self._observe(epoch)
            entry = self._entries.get(key)
            if entry is None:
                return False, None
            expires, value = entry
            if time.monotonic() >= expires:
                del self._entries[key]
                return False, None
            self._entries.move_to_end(key)
        return True, copy.deepcopy(value)

    def put(self, key: _Key, value: object, epoch: str, generation: int) -> None:
        """Store a copy of ``value`` unless the cache was cleared since ``generation``; evict the LRU entry when full."""
        stored = copy.deepcopy(value)
        with self._lock:
            if generation != self._generation:
                return
            self._observe(epoch)
            now = time.monotonic()
            for stale in [k for k, (expires, _) in self._entries.items() if now >= expires]:
                del self._entries[stale]
            self._entries[key] = (now + CACHE_TTL_SECONDS, stored)
            self._entries.move_to_end(key)
            while len(self._entries) > CACHE_MAX_ENTRIES:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        """Drop every entry and advance the generation."""
        with self._lock:
            self._entries.clear()
            self._generation += 1

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


_cache = _ReplyCache()
_slots = threading.BoundedSemaphore(DETECTION_PARALLELISM)
_pool: Optional[ThreadPoolExecutor] = None
_pool_lock = threading.Lock()


async def clear_detection_cache() -> None:
    """Forget every cached reply in every worker; called when a document or a user is erased.

    This worker's entries go at once; the shared erasure epoch is replaced
    (:func:`~.document_anon_store.bump_erasure_epoch`), which raises when it
    cannot be, so other workers miss on every entry stored before.
    """
    _cache.clear()
    await bump_erasure_epoch()


def shutdown_detection_runner() -> None:
    """Stop the dispatch pool, cancelling calls not yet started; a later batch starts a new one."""
    global _pool
    with _pool_lock:
        pool, _pool = _pool, None
    if pool is not None:
        pool.shutdown(wait=False, cancel_futures=True)


def _executor() -> ThreadPoolExecutor:
    """The dispatch pool, started on first use."""
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(
                max_workers=DETECTION_PARALLELISM, thread_name_prefix="septum-ollama-detect"
            )
        return _pool


def _target(call: DetectionCall[T]) -> _Target:
    """``(prompt fingerprint, server, model)`` of ``call``.

    The fingerprint hashes the prompt rendered with :data:`~.prompts.DATA_NONCE_SENTINEL`,
    so it covers the text, template and parameters and holds no content.
    """
    rendered = call.prompt(DATA_NONCE_SENTINEL)
    digest = hashlib.sha256(json.dumps([rendered.system, rendered.user]).encode("utf-8"))
    return (digest.hexdigest(), *ollama_target(call.base_url, call.model))


class _CallFailed(Exception):
    """A call got no usable reply; ``kind`` is the content-free failure kind."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


class _Stopped(Exception):
    """A call was abandoned because another call of its batch failed."""


def _parsed(call: DetectionCall[T], reply: str) -> Optional[T]:
    """``call.parse(reply)``; a parser error counts as an unparsable reply and is logged by class."""
    try:
        return call.parse(reply)
    except Exception as exc:
        logger.warning("A local model detection reply could not be parsed: %s", type(exc).__name__)
        return None


def _attempt(call: DetectionCall[T], send: Callable[..., str]) -> tuple[Optional[T], str, bool]:
    """Send ``call`` once: ``(parsed, "", False)`` on success, else ``(None, kind, retryable)``."""
    rendered = call.prompt(new_data_nonce())
    try:
        reply = send(
            **rendered.request_kwargs(),
            base_url=call.base_url,
            model=call.model,
            allow_truncated=False,
        )
    except OllamaCallError as exc:
        return None, str(exc), exc.retryable
    parsed = _parsed(call, reply)
    if parsed is None:
        return None, UNPARSABLE_REPLY, True
    return parsed, "", False


def _execute(call: DetectionCall[T], send: Callable[..., str], stop: threading.Event) -> T:
    """Run one call to a parsed reply, retrying once; return it, or raise :class:`_CallFailed`.

    The retry is reserved against the call budget before it is sent.
    """
    for attempt in range(1, _ATTEMPTS + 1):
        if attempt > 1:
            if stop.wait(RETRY_BACKOFF_SECONDS):
                raise _Stopped()
            reserve_ollama_calls(1)
        with _slots:
            if stop.is_set():
                raise _Stopped()
            parsed, kind, retryable = _attempt(call, send)
        if parsed is not None:
            return parsed
        if not retryable or attempt == _ATTEMPTS:
            raise _CallFailed(kind)
        logger.info("Retrying a local model detection call after %s", kind)


def _first_failure(done: List[Future[T]]) -> Optional[BaseException]:
    """The error of a finished call, else :class:`_CallFailed` for a cancelled one, else ``None``."""
    cancelled = False
    for future in done:
        if future.cancelled():
            cancelled = True
            continue
        error = future.exception()
        if error is not None:
            return error
    return _CallFailed(CANCELLED_CALL) if cancelled else None


def _dispatch(misses: dict[_Target, DetectionCall[T]], send: Callable[..., str]) -> dict[_Target, T]:
    """Send every miss, a single one on the calling thread; raise on the first failure."""
    stop = threading.Event()
    if len(misses) == 1:
        ((target, call),) = misses.items()
        return {target: _execute(call, send, stop)}
    pool = _executor()
    futures: dict[Future[T], _Target] = {
        pool.submit(contextvars.copy_context().run, _execute, call, send, stop): target
        for target, call in misses.items()
    }
    _wait_for_first_failure(list(futures))
    done = [future for future in futures if future.done()]
    error = _first_failure(done)
    if error is not None:
        stop.set()
        for future in futures:
            future.cancel()
        raise error
    return {target: future.result() for future, target in futures.items()}


def _wait_for_first_failure(futures: List[Future[T]]) -> None:
    """Block until every call has finished, or one has failed or been cancelled.

    Uses done callbacks, not :func:`concurrent.futures.wait`, which a pool
    shutdown (``cancel_futures=True``) never wakes.
    """
    settled = threading.Event()
    lock = threading.Lock()
    remaining = len(futures)

    def _on_done(future: Future[T]) -> None:
        nonlocal remaining
        with lock:
            remaining -= 1
            if remaining == 0 or future.cancelled() or future.exception() is not None:
                settled.set()

    for future in futures:
        future.add_done_callback(_on_done)
    settled.wait()


def run_detection_calls(
    calls: Sequence[DetectionCall[T]],
    *,
    send: Callable[..., str],
    refusal: Callable[[str], Exception],
) -> List[T]:
    """Return the parsed reply of every call, in order, or raise ``refusal(kind)``.

    ``send`` is the caller's ``request_ollama_sync``, looked up in its own
    module so a test that replaces it there still intercepts. Identical calls
    are sent once. A budget refusal is raised as it is, before the call it
    would pay for; a failed, unparsable or cancelled call is ``refusal(kind)``.
    """
    scope = _cache_scope.get()
    generation = _cache.generation()
    targets = [_target(call) for call in calls]
    results: dict[_Target, T] = {}
    misses: dict[_Target, DetectionCall[T]] = {}
    keys: dict[_Target, _Key] = {}
    for target, call in zip(targets, calls):
        if target in results or target in misses:
            continue
        if scope is not None:
            keys[target] = (scope.owner, *target)
            found, value = _cache.get(keys[target], scope.epoch)
            if found:
                results[target] = cast(T, value)
                continue
        misses[target] = call
    reserve_ollama_calls(len(misses))
    if misses:
        try:
            fresh = _dispatch(misses, send)
        except _CallFailed as exc:
            raise refusal(exc.kind) from exc
        if scope is not None:
            for target, value in fresh.items():
                _cache.put(keys[target], value, scope.epoch, generation)
        results.update(fresh)
    returned: set[_Target] = set()
    ordered: List[T] = []
    for target in targets:
        value = results[target]
        ordered.append(copy.deepcopy(value) if target in returned else value)
        returned.add(target)
    return ordered


__all__ = [
    "CACHE_MAX_ENTRIES",
    "CACHE_TTL_SECONDS",
    "DETECTION_PARALLELISM",
    "DetectionCall",
    "RETRY_BACKOFF_SECONDS",
    "clear_detection_cache",
    "detection_cache_scope",
    "local_model_scope",
    "run_detection_calls",
    "shutdown_detection_runner",
]
