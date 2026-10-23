"""Consumer heartbeat sidecar: whether the ingest consumer is storing records or backing off.

The file holds the time of the last stored record, the current failure streak and when
it began, and the time of the last write. It never holds record content. One consumer
process writes a given ledger's sidecar.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .events import require_count, require_finite_number
from .fsutil import read_sidecar_json, sibling, write_sidecar


@dataclass(frozen=True)
class ConsumerStatus:
    last_ingest_at: float | None
    consecutive_failures: int
    first_failure_at: float | None
    checked_at: float


def consumer_status_path(ledger: str | os.PathLike[str]) -> Path:
    return sibling(ledger, ".consumer")


def write_consumer_status(ledger: str | os.PathLike[str], status: ConsumerStatus) -> None:
    write_sidecar(consumer_status_path(ledger), status)


def read_consumer_status(ledger: str | os.PathLike[str]) -> ConsumerStatus | None:
    """Return the last written consumer status, or ``None`` when none exists yet.

    Raises ``ValueError`` when the file exists but is malformed. The message names the
    file only.
    """
    path = consumer_status_path(ledger)
    data = read_sidecar_json(path, "consumer status")
    if data is None:
        return None
    try:
        last = data["last_ingest_at"]
        failures = data["consecutive_failures"]
        first = data["first_failure_at"]
        checked = data["checked_at"]
        require_count(failures)
        require_finite_number(checked)
        if last is not None:
            require_finite_number(last)
        if first is not None:
            require_finite_number(first)
        return ConsumerStatus(
            last_ingest_at=None if last is None else float(last),
            consecutive_failures=failures,
            first_failure_at=None if first is None else float(first),
            checked_at=float(checked),
        )
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        raise ValueError(f"consumer status is malformed: {path.name}") from exc
