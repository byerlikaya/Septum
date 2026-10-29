"""Retention anchor: the chain checkpoint a pruned jsonl ledger verifies from.

Pruning removes the oldest records, so the first surviving record links to a
hash that is no longer in the file. The anchor sidecar (``<ledger>.anchor``)
records that hash so verification starts there instead of at genesis. A missing
or malformed anchor is never ignored: a pruned ledger without a readable anchor
fails verification.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .events import (
    GENESIS_PREV_HASH,
    LedgerIntegrityError,
    loads_strict,
    require_count,
    require_finite_number,
)
from .fsutil import atomic_write_private, open_ledger_file, sibling

_HASH_RE = re.compile(r"[0-9a-f]{64}")


def is_hash(value: object) -> bool:
    """True when ``value`` is exactly one sha256 hex digest (no surrounding whitespace or newline)."""
    return isinstance(value, str) and _HASH_RE.fullmatch(value) is not None


@dataclass(frozen=True)
class RetentionAnchor:
    """Chain state recorded by the last prune.

    ``hash`` is the link the surviving ledger starts from. ``previous_hash`` is
    the link it started from before that prune, so a prune that crashed before
    the ledger rewrite still verifies. ``removed_total`` is cumulative.
    ``legacy_prefix`` marks a genesis anchor whose ledger still starts with
    hash-less lines from the v1.0-v1.1 format.
    """

    hash: str
    previous_hash: str
    removed_total: int
    pruned_at: float
    legacy_prefix: bool = False

    def __post_init__(self) -> None:
        if not is_hash(self.hash) or not is_hash(self.previous_hash):
            raise ValueError("not a sha256 hex digest")
        require_count(self.removed_total)
        require_finite_number(self.pruned_at)
        if not isinstance(self.legacy_prefix, bool):
            raise ValueError("legacy_prefix is not a boolean")
        if self.legacy_prefix and (self.hash != GENESIS_PREV_HASH or self.previous_hash != GENESIS_PREV_HASH):
            raise ValueError("legacy_prefix is only valid on a genesis anchor")


def anchor_path(ledger: str | os.PathLike[str]) -> Path:
    return sibling(ledger, ".anchor")


def read_anchor(ledger: str | os.PathLike[str]) -> RetentionAnchor | None:
    """Return the anchor next to ``ledger``, or ``None`` when no prune has happened.

    Raises :class:`LedgerIntegrityError` when the file exists but is not a valid
    anchor. The message names the file and never echoes its content.
    """
    path = anchor_path(ledger)
    try:
        with open_ledger_file(path, os.O_RDONLY, "r") as fh:
            raw = fh.read()
    except FileNotFoundError:
        return None
    try:
        data = loads_strict(raw)
        return RetentionAnchor(
            hash=data["hash"],
            previous_hash=data["previous_hash"],
            removed_total=data["removed_total"],
            pruned_at=data["pruned_at"],
            legacy_prefix=data.get("legacy_prefix", False),
        )
    except (ValueError, KeyError, TypeError):
        raise LedgerIntegrityError(f"retention anchor is malformed: {path.name}") from None


def write_anchor(ledger: str | os.PathLike[str], anchor: RetentionAnchor) -> None:
    """Atomically replace the anchor next to ``ledger``."""
    atomic_write_private(anchor_path(ledger), json.dumps(asdict(anchor), sort_keys=True) + "\n")


def accepted_links(anchor: RetentionAnchor | None) -> tuple[str, ...]:
    """Links the first record of a ledger may carry for verification to pass.

    The None case returns GENESIS twice on purpose, to keep a fixed two-element shape.
    """
    if anchor is None:
        return (GENESIS_PREV_HASH, GENESIS_PREV_HASH)
    return (anchor.hash, anchor.previous_hash)


def new_anchor(
    *,
    hash: str,
    previous_hash: str,
    removed: int,
    earlier: RetentionAnchor | None,
    now: float | None,
    legacy_prefix: bool = False,
) -> RetentionAnchor:
    """Build the anchor for a prune that removed ``removed`` pruned records (torn fragments excluded)."""
    total = removed + (earlier.removed_total if earlier is not None else 0)
    return RetentionAnchor(
        hash=hash,
        previous_hash=previous_hash,
        removed_total=total,
        pruned_at=now if now is not None else time.time(),
        legacy_prefix=legacy_prefix,
    )
