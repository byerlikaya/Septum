"""The committed retrieval baseline must describe the current evaluation fixtures."""

from __future__ import annotations

import json

from tests.retrieval_eval import EVAL_DIR, fixture_digest
from tests.retrieval_report import SCHEMA_VERSION

BASELINE = EVAL_DIR / "baseline_minilm.json"
RERECORD_HINT = (
    "re-record tests/retrieval_eval/baseline_minilm.json: run tests/benchmark_retrieval.py "
    "-k full_retrieval with SEPTUM_BENCHMARK_OUT pointing at that file"
)


def _baseline() -> dict:
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def test_baseline_digest_matches_fixtures() -> None:
    assert _baseline()["eval"]["digest"] == fixture_digest(), (
        f"the evaluation fixtures changed since the baseline was recorded; {RERECORD_HINT}"
    )


def test_baseline_schema_matches_report() -> None:
    assert _baseline()["schema_version"] == SCHEMA_VERSION, (
        f"the baseline schema is out of date with tests/retrieval_report.py; {RERECORD_HINT}"
    )
