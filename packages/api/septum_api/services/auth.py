from __future__ import annotations

"""JWT token and password hashing utilities."""

import asyncio
import logging
import math
import os
import secrets
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from functools import cache
from pathlib import Path
from typing import Callable, Literal, TypeVar

import bcrypt
import jwt

from ..bootstrap import get_config

JWT_ALGORITHM = "HS256"
# 12 chars + bcrypt cost ~12 bring brute-force outside trivial GPU
# range while still being typeable. Combined with the per-route
# rate-limit on /api/auth/login this is the practical defence.
PASSWORD_MIN_LENGTH = 12
# bcrypt only reads the first 72 bytes and bcrypt>=5 raises beyond that.
PASSWORD_MAX_BYTES = 72


class WeakPasswordError(ValueError):
    """Raised when a password does not satisfy the strength policy."""


def validate_password_strength(password: str) -> None:
    """Raise :class:`WeakPasswordError` if *password* is too short, too long or not encodable."""
    if len(password) < PASSWORD_MIN_LENGTH:
        raise WeakPasswordError(
            f"Password must be at least {PASSWORD_MIN_LENGTH} characters long"
        )
    try:
        encoded = password.encode()
    except UnicodeEncodeError as exc:
        raise WeakPasswordError("Password contains invalid characters") from exc
    if len(encoded) > PASSWORD_MAX_BYTES:
        raise WeakPasswordError(
            f"Password must be at most {PASSWORD_MAX_BYTES} bytes long"
        )


def _jwt_secret() -> str:
    return get_config().jwt_secret_key


def _jwt_expiration_minutes() -> int:
    return get_config().jwt_expiration_minutes


def hash_password(plain: str) -> str:
    """Hash a plaintext password with bcrypt."""
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against a bcrypt hash.

    Input is cut to :data:`PASSWORD_MAX_BYTES` exactly as bcrypt<5 did
    implicitly, so hashes created from longer passwords keep verifying
    and an over-long login attempt fails cleanly instead of raising.
    Input that cannot be UTF-8 encoded (lone surrogates) never matches.
    """
    try:
        encoded = plain.encode()
    except UnicodeEncodeError:
        return False
    return bcrypt.checkpw(encoded[:PASSWORD_MAX_BYTES], hashed.encode())


# Same cost factor as real hashes, so a lookup miss costs one full bcrypt
# check and login timing does not reveal whether an email is registered.
@cache
def _dummy_password_hash() -> str:
    """Return the timing-equalizer hash, created on first use instead of at import."""
    return bcrypt.hashpw(secrets.token_bytes(32), bcrypt.gensalt()).decode()


def warm_dummy_password_hash() -> None:
    """Build the timing-equalizer hash now, so the first login miss costs the same as later ones."""
    _dummy_password_hash()


def verify_password_or_dummy(plain: str, hashed: str | None) -> bool:
    """Verify against *hashed*, or burn an equal-cost check and return False when it is None."""
    if hashed is None:
        verify_password(plain, _dummy_password_hash())
        return False
    return verify_password(plain, hashed)


_Result = TypeVar("_Result")
PasswordLane = Literal["public", "account", "admin"]

logger = logging.getLogger(__name__)

_password_executor: ThreadPoolExecutor | None = None
_password_executor_lock = threading.Lock()

# Queued-or-running bcrypt jobs, split in two lanes so signed-in users can
# never take the capacity anonymous logins need: "account" work (change
# password, admin create/reset) gets at most one slot per worker, "public"
# work (login, register) the rest. One in-flight job per identity.
_state_lock = threading.Lock()
_pending_public = 0
_pending_account = 0
_in_flight: set[str] = set()
# Admin work waiting for an account slot, oldest first.
_account_waiters: deque[asyncio.Future] = deque()

PASSWORD_QUEUE_PER_WORKER = 8
PASSWORD_QUEUE_MIN = 16
PASSWORD_BUSY_RETRY_AFTER_SECONDS = 2

_CGROUP_ROOT = Path("/sys/fs/cgroup")
_cgroup_warning_logged = False


class PasswordWorkBusy(Exception):
    """The bcrypt queue (or this caller's share of it) is full; refused without hashing."""


def _read_cgroup_quota(path: Path, period_path: Path | None = None) -> int | None:
    """Return ceil(quota / period) from cgroup files, or None when unlimited or absent.

    ``period_path`` is None for cgroup v2 (``cpu.max`` holds both values).
    A missing file is normal (no cgroup limit); an unreadable or malformed
    one is logged once by class name and ignored.
    """
    global _cgroup_warning_logged  # noqa: PLW0603
    try:
        if period_path is None:
            quota_text, period_text = path.read_text().split()
        else:
            quota_text, period_text = path.read_text().strip(), period_path.read_text().strip()
        if quota_text in ("max", "-1"):
            return None
        quota, period = int(quota_text), int(period_text)
        if quota <= 0 or period <= 0:
            return None
        return math.ceil(quota / period)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        if not _cgroup_warning_logged:
            logger.warning("Ignoring unreadable cgroup CPU quota: %s", type(exc).__name__)
            _cgroup_warning_logged = True
        return None


def _process_cpus() -> int:
    """CPUs this process may be scheduled on (affinity-aware where the platform says so)."""
    process_cpu_count = getattr(os, "process_cpu_count", None)
    if process_cpu_count is not None:
        return process_cpu_count() or 1
    sched_getaffinity = getattr(os, "sched_getaffinity", None)
    if sched_getaffinity is not None:
        return len(sched_getaffinity(0)) or 1
    return os.cpu_count() or 1


def available_cpus(cgroup_root: Path = _CGROUP_ROOT) -> int:
    """CPUs this process may actually use: affinity and the cgroup v2/v1 quota, whichever is lower."""
    counts = [_process_cpus()]
    for quota in (
        _read_cgroup_quota(cgroup_root / "cpu.max"),
        _read_cgroup_quota(
            cgroup_root / "cpu" / "cpu.cfs_quota_us", cgroup_root / "cpu" / "cpu.cfs_period_us"
        ),
    ):
        if quota is not None:
            counts.append(quota)
    return max(1, min(counts))


@cache
def _password_workers() -> int:
    return max(2, available_cpus() // 2)


def password_queue_capacity() -> int:
    """How many bcrypt jobs may be queued or running in total."""
    return max(PASSWORD_QUEUE_MIN, _password_workers() * PASSWORD_QUEUE_PER_WORKER)


def account_queue_capacity() -> int:
    """How many of those slots signed-in password work (change, admin create/reset) may hold."""
    return _password_workers()


def _get_password_executor() -> ThreadPoolExecutor:
    """Return the bcrypt pool, created on first use.

    bcrypt gets its own small pool so a burst of logins queues behind
    itself instead of delaying retrieval, ingestion and PII work that
    share asyncio's default executor.
    """
    global _password_executor  # noqa: PLW0603
    with _password_executor_lock:
        if _password_executor is None:
            _password_executor = ThreadPoolExecutor(
                max_workers=_password_workers(), thread_name_prefix="bcrypt"
            )
        return _password_executor


def _try_reserve(lane: PasswordLane, identity: str | None) -> bool:
    """Take a public or account slot without waiting; False when refused."""
    global _pending_public, _pending_account  # noqa: PLW0603
    with _state_lock:
        if identity is not None and identity in _in_flight:
            return False
        if lane == "public":
            if _pending_public >= password_queue_capacity() - account_queue_capacity():
                return False
            _pending_public += 1
        else:
            # Waiting admin work is served first: no new account work jumps it.
            if _account_waiters or _pending_account >= account_queue_capacity():
                return False
            _pending_account += 1
        if identity is not None:
            _in_flight.add(identity)
        return True


def _grant(waiter: asyncio.Future) -> None:
    if not waiter.done():
        waiter.set_result(None)


def _free_account_slot_locked() -> None:
    """Hand a freed account slot to the oldest waiter, or return it (caller holds the lock)."""
    global _pending_account  # noqa: PLW0603
    while _account_waiters:
        waiter = _account_waiters.popleft()
        try:
            # Slots are freed on pool threads; wake the waiter on its own loop.
            waiter.get_loop().call_soon_threadsafe(_grant, waiter)
            return
        except RuntimeError:
            continue  # that waiter's loop is gone; try the next one
    _pending_account -= 1


async def _wait_for_account_slot() -> None:
    """Take an account slot, waiting in FIFO order behind earlier admin work.

    A freed slot passes straight to the oldest waiter (it stays counted),
    so neither new account submissions nor later waiters can take it
    first. Cancelling the wait gives back a slot that was already granted.
    """
    global _pending_account  # noqa: PLW0603
    with _state_lock:
        if not _account_waiters and _pending_account < account_queue_capacity():
            _pending_account += 1
            return
        waiter = asyncio.get_running_loop().create_future()
        _account_waiters.append(waiter)
    try:
        await waiter
    except asyncio.CancelledError:
        with _state_lock:
            try:
                _account_waiters.remove(waiter)
            except ValueError:
                _free_account_slot_locked()  # granted while being cancelled
        raise


def _release(lane: PasswordLane, identity: str | None) -> None:
    global _pending_public  # noqa: PLW0603
    with _state_lock:
        if lane == "public":
            _pending_public -= 1
        else:
            _free_account_slot_locked()
        if identity is not None:
            _in_flight.discard(identity)


async def run_password_work(
    func: Callable[..., _Result],
    *args: object,
    lane: PasswordLane = "admin",
    identity: str | None = None,
) -> _Result:
    """Run a bcrypt hash or verify on the dedicated pool, off the event loop.

    ``public`` (login, register) and ``account`` (change password) work is
    refused with :class:`PasswordWorkBusy` instead of queueing when its
    lane is full or *identity* already has a job in flight, so a flood
    gets fast refusals and a signed-in user cannot starve logins. The
    decision is made before any bcrypt runs and never looks at the
    arguments, so it reveals nothing about whether an account exists.
    ``admin`` work (admin create/reset: needs an admin session, rare)
    waits in FIFO order for an account slot instead of being refused. A
    slot is freed when its job finishes or is cancelled; a caller that
    goes away cancels its job if it has not started.
    """
    if lane == "admin":
        if identity is not None:
            raise ValueError("admin password work is not tracked per identity")
        await _wait_for_account_slot()
    elif not _try_reserve(lane, identity):
        raise PasswordWorkBusy
    try:
        job = _get_password_executor().submit(func, *args)
    except BaseException:
        _release(lane, identity)
        raise
    job.add_done_callback(lambda _: _release(lane, identity))
    return await asyncio.wrap_future(job)


def shutdown_password_executor() -> None:
    """Stop the bcrypt pool (called at shutdown); the next use creates a fresh one.

    Jobs not yet started are cancelled, which releases their slots.
    """
    global _password_executor  # noqa: PLW0603
    with _password_executor_lock:
        executor, _password_executor = _password_executor, None
    if executor is not None:
        executor.shutdown(wait=False, cancel_futures=True)


def create_access_token(user_id: int, email: str) -> str:
    """Create a signed JWT access token."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=_jwt_expiration_minutes())
    payload = {
        "sub": str(user_id),
        "email": email,
        "exp": expire,
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Decode and verify a JWT access token.

    Raises ``jwt.InvalidTokenError`` on failure.
    """
    return jwt.decode(
        token,
        _jwt_secret(),
        algorithms=[JWT_ALGORITHM],
        options={"require": ["exp", "sub"]},
    )
