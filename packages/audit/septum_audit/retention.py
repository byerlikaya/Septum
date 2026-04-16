"""Age- and count-based retention for jsonl sinks.

Compliance regimes (GDPR Art. 5(1)(e), KVKK m. 7) require audit data to
be deleted once its retention purpose is served. The :class:`RetentionPolicy`
encodes both knobs an operator usually wants:

* ``max_age_days`` — drop records older than N days (rolling window).
* ``max_records`` — keep at most N records (caps unbounded growth).

Both can be set together — the stricter rule wins on each pass. The
algorithm streams the source file once, decides per-line, and writes
the kept lines to a sibling temp file before atomically swapping it
into place. A crash mid-rewrite leaves the original file untouched.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RetentionPolicy:
    """Combination of an age cap and a count cap.

    Either field may be ``None`` to disable that dimension. A policy
    with both fields ``None`` is a no-op (the apply call returns 0).
    """

    max_age_days: int | None = None
    max_records: int | None = None

    @property
    def is_noop(self) -> bool:
        return self.max_age_days is None and self.max_records is None

    def cutoff_timestamp(self, now: float | None = None) -> float | None:
        """Wall-clock cutoff below which records are dropped, or ``None``."""
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
    """Rewrite ``path`` in place, dropping records that violate the policy.

    Returns the number of records removed. The rewrite is atomic — a
    crash mid-pass leaves the original file untouched. Missing files
    return 0 silently so a freshly-deployed service can call this from
    a startup hook without first touching the log.
    """
    if policy.is_noop:
        return 0

    src = Path(path)
    if not src.exists():
        return 0

    cutoff = policy.cutoff_timestamp(now=now)
    kept: list[str] = []
    removed = 0

    with src.open("r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                # Truncated or corrupt lines are dropped — they would
                # break the next exporter pass anyway.
                removed += 1
                continue
            if cutoff is not None and float(payload.get("timestamp", 0.0)) < cutoff:
                removed += 1
                continue
            kept.append(line)

    if policy.max_records is not None and len(kept) > policy.max_records:
        # Keep the most recent N — the file is append-ordered so the
        # tail is newest. ``removed`` reflects the cumulative trim.
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
