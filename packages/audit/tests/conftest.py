"""Per-test watchdog: a test that blocks fails after a bound instead of hanging the suite."""

from __future__ import annotations

import faulthandler
import signal

import pytest

TEST_TIMEOUT_SECONDS = 60


class TestBlocked(Exception):
    """Raised in the main thread when a test exceeds the watchdog bound."""

    __test__ = False


@pytest.fixture(autouse=True)
def _watchdog():
    def _expired(_signum, _frame):
        raise TestBlocked(f"test exceeded {TEST_TIMEOUT_SECONDS}s")

    previous = signal.signal(signal.SIGALRM, _expired)
    signal.alarm(TEST_TIMEOUT_SECONDS)
    # Backstop for a hang the signal cannot interrupt (a blocking C call).
    faulthandler.dump_traceback_later(TEST_TIMEOUT_SECONDS + 30, exit=True)
    try:
        yield
    finally:
        faulthandler.cancel_dump_traceback_later()
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
