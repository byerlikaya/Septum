"""Checks for the retrieval delta report (tests/retrieval_report.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.retrieval_report import compare_runs, main


def _report_run(digest: str, recall: float, schema_version: int = 1) -> dict:
    return {
        "schema_version": schema_version,
        "eval": {"digest": digest},
        "results": {
            "hybrid": {
                "sanitized": {
                    "overall": {"recall@5": {"mean": recall, "ci95": [recall - 0.1, recall + 0.1]}},
                    "negatives": {"false_retrieval": {"mean": 0.25, "ci95": [0.0, 0.5]}},
                }
            }
        },
        "leak": {"en": {"crossing": {"rate": 0.2}, "control": {"rate": 0.1}, "difference": 0.1}},
        "timing": {"wall_s": 1.0},
    }


def test_report_identical_runs_have_zero_deltas(tmp_path: Path) -> None:
    run = _report_run("abc", 0.6)
    rows = compare_runs(run, json.loads(json.dumps(run)))
    assert {path for path, *_ in rows} >= {
        "results.hybrid.sanitized.overall.recall@5.mean",
        "leak.en.crossing.rate",
    }
    assert all(delta == 0.0 for _, _, _, delta in rows)

    first = tmp_path / "a.json"
    second = tmp_path / "b.json"
    first.write_text(json.dumps(run), encoding="utf-8")
    second.write_text(json.dumps(run), encoding="utf-8")
    assert main([str(first), str(second)]) == 0


def test_report_shows_nonzero_delta_for_changed_mean() -> None:
    rows = compare_runs(_report_run("abc", 0.6), _report_run("abc", 0.75))
    deltas = {path: delta for path, _, _, delta in rows}
    assert deltas["results.hybrid.sanitized.overall.recall@5.mean"] == pytest.approx(0.15)
    assert deltas["leak.en.crossing.rate"] == 0.0


def test_report_refuses_digest_mismatch(tmp_path: Path) -> None:
    first = tmp_path / "a.json"
    second = tmp_path / "b.json"
    first.write_text(json.dumps(_report_run("abc", 0.6)), encoding="utf-8")
    second.write_text(json.dumps(_report_run("xyz", 0.6)), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        main([str(first), str(second)])
    assert exc.value.code != 0


def test_report_refuses_schema_mismatch(tmp_path: Path) -> None:
    first = tmp_path / "a.json"
    second = tmp_path / "b.json"
    first.write_text(json.dumps(_report_run("abc", 0.6)), encoding="utf-8")
    second.write_text(json.dumps(_report_run("abc", 0.6, schema_version=2)), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        main([str(first), str(second)])
    assert exc.value.code != 0
