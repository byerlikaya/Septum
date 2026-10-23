"""Content-free retention status sidecar, written by the worker and read by /health.

The file holds a state word, the time of the last successful pass, a failure
counter, the time of the last check and the worker's interval. It never holds
record content, so it is safe to expose through the health endpoint.

``ok`` means the last retention pass succeeded. It does not mean the ledger
was verified: a pass that has nothing to prune skips verification. One worker
process writes a given ledger, and the status describes that process only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .events import require_count, require_finite_number
from .fsutil import read_sidecar_json, sibling, write_sidecar

STATES = ("disabled", "ok", "halted", "failed", "blocked")


@dataclass(frozen=True)
class RetentionStatus:
    state: str
    last_success_at: float | None
    consecutive_failures: int
    checked_at: float
    interval_seconds: int


def status_path(ledger: str | os.PathLike[str]) -> Path:
    return sibling(ledger, ".status")


def write_status(ledger: str | os.PathLike[str], status: RetentionStatus) -> None:
    if status.state not in STATES:
        raise ValueError(f"unknown retention state: {status.state}")
    write_sidecar(status_path(ledger), status)


def read_status(ledger: str | os.PathLike[str]) -> RetentionStatus | None:
    """Return the last written status, or ``None`` when no worker has reported yet.

    Raises ``ValueError`` when the file exists but cannot be parsed or has fields
    of the wrong type. The message names the file only.
    """
    path = status_path(ledger)
    data = read_sidecar_json(path, "retention status")
    if data is None:
        return None
    try:
        state = data["state"]
        if state not in STATES:
            raise ValueError("unknown state")
        last = data["last_success_at"]
        checked = data["checked_at"]
        failures = data["consecutive_failures"]
        interval = data["interval_seconds"]
        require_finite_number(checked)
        require_count(failures)
        require_count(interval)
        if last is not None:
            require_finite_number(last)
        return RetentionStatus(
            state=state,
            last_success_at=None if last is None else float(last),
            consecutive_failures=failures,
            checked_at=float(checked),
            interval_seconds=interval,
        )
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        raise ValueError(f"retention status is malformed: {path.name}") from exc
