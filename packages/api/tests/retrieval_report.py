"""Delta report for retrieval benchmark JSON runs.

Usage (from packages/api):  python -m tests.retrieval_report base.json other.json [...]

The first file is the baseline; every other run is shown as a delta against
it. Runs are refused (non-zero exit) when their schema versions differ or
their evaluation-set digests differ, because then the numbers measure
different questions.

Only the quality sections are compared (``results`` and ``leak``): timings
and run metadata are expected to differ between runs.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable, Optional

SCHEMA_VERSION = 1
COMPARED_KEYS = ("results", "leak")
RATE_LEAVES = frozenset({"mean", "rate", "difference"})


def load_run(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def check_comparable(runs: list[dict], names: list[str]) -> None:
    """Raise SystemExit when a run has another schema or evaluation digest than the first."""
    base = runs[0]
    for name, run in zip(names, runs):
        if run.get("schema_version") != SCHEMA_VERSION:
            raise SystemExit(
                f"{name}: schema_version {run.get('schema_version')!r} is not {SCHEMA_VERSION}"
            )
        if run["eval"]["digest"] != base["eval"]["digest"]:
            raise SystemExit(
                f"{name}: evaluation digest {run['eval']['digest'][:12]} differs from "
                f"{names[0]} ({base['eval']['digest'][:12]}); the runs measure different questions"
            )


def _rate_leaves(node: object, path: tuple[str, ...] = ()) -> Iterable[tuple[str, float]]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _rate_leaves(value, path + (key,))
    elif isinstance(node, (int, float)) and not isinstance(node, bool) and path and path[-1] in RATE_LEAVES:
        yield ".".join(path), float(node)


def flatten(run: dict) -> dict[str, float]:
    """Headline numbers keyed by path: means, rates and differences from the quality sections."""
    flat: dict[str, float] = {}
    for section in COMPARED_KEYS:
        flat.update(dict(_rate_leaves(run.get(section, {}), (section,))))
    return flat


def compare_runs(base: dict, other: dict) -> list[tuple[str, Optional[float], Optional[float], Optional[float]]]:
    """Rows of (path, baseline value, other value, delta); a missing value gives a None delta."""
    base_flat = flatten(base)
    other_flat = flatten(other)
    rows = []
    for path in sorted(set(base_flat) | set(other_flat)):
        b = base_flat.get(path)
        o = other_flat.get(path)
        delta = o - b if b is not None and o is not None else None
        rows.append((path, b, o, delta))
    return rows


def render_delta(names: list[str], runs: list[dict]) -> str:
    base_name = names[0]
    header = ["metric", base_name] + [f"{name} (Δ vs {base_name})" for name in names[1:]]
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    flats = [flatten(run) for run in runs]
    for path in sorted(set().union(*flats)):
        row = [path]
        base_value = flats[0].get(path)
        row.append("-" if base_value is None else f"{base_value:.3f}")
        for flat in flats[1:]:
            value = flat.get(path)
            if base_value is None or value is None:
                row.append("-")
            else:
                row.append(f"{value:.3f} ({value - base_value:+.3f})")
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("files", nargs="+", type=Path, help="benchmark JSON files; the first is the baseline")
    args = parser.parse_args(argv)
    names = [str(path) for path in args.files]
    runs = [load_run(path) for path in args.files]
    check_comparable(runs, names)
    print(render_delta(names, runs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
