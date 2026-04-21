"""Age- and count-based retention for jsonl sinks."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RetentionPolicy:
    """Age cap and/or count cap. ``None`` disables that dimension."""

    max_age_days: int | None = None
    max_records: int | None = None

    @property
    def is_noop(self) -> bool:
        return self.max_age_days is None and self.max_records is None

    def cutoff_timestamp(self, now: float | None = None) -> float | None:
        if self.max_age_days is None:
            return None
        reference = now if now is not None else time.time()
        return reference - (self.max_age_days * 86400.0)


def apply_retention_to_jsonl(
    path: str | os.PathLike[str],
    policy: RetentionPolicy,
    *,
    now: float | None = None,
) -> int:
    """Rewrite ``path`` in place, dropping records that violate ``policy``.

    Returns the number of records removed. The rewrite goes through a
    ``.tmp`` sibling + :func:`os.replace` so a crash mid-pass leaves the
    original untouched. Corrupt lines count as removals.
    """
    if policy.is_noop:
        return 0

    src = Path(path)
    cutoff = policy.cutoff_timestamp(now=now)
    kept: list[str] = []
    removed = 0

    try:
        fh = src.open("r", encoding="utf-8")
    except FileNotFoundError:
        return 0

    with fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                removed += 1
                continue
            if cutoff is not None and float(payload.get("timestamp", 0.0)) < cutoff:
                removed += 1
                continue
            kept.append(line)

    if policy.max_records is not None and len(kept) > policy.max_records:
        removed += len(kept) - policy.max_records
        kept = kept[-policy.max_records :]

    if removed == 0:
        return 0

    tmp = src.with_suffix(src.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for line in kept:
            fh.write(line + "\n")
    os.replace(tmp, src)
    return removed
