"""Regression test: a hard OCR worker death must not hang document ingestion.

The OCR worker runs in a spawn-context process pool. When that process dies
without raising (a native segfault or ``os._exit``), the pool must report the
failure, the call must fall back like any other worker failure, and the next
call must get a fresh pool. The worker functions below run in a real spawned
child, so the test exercises genuine process death, not a fake.
"""

from __future__ import annotations

import os
import threading
from typing import Any, Dict, List

import numpy as np
import pytest

from septum_api.services.ingestion.ocr import paddle_provider
from septum_api.services.ingestion.ocr.paddle_provider import (
    PaddleOcrProvider,
    shutdown_ocr_pool,
)

_CALL_DEADLINE_SECONDS = 120


def _die_hard(*_args: Any) -> List[Dict[str, Any]]:
    os._exit(1)


def _return_one_line(*_args: Any) -> List[Dict[str, Any]]:
    return [{"rec_texts": ["ok"], "rec_scores": [0.9]}]


def _call_with_deadline(provider: PaddleOcrProvider, image: np.ndarray) -> Any:
    outcome: Dict[str, Any] = {}

    def run() -> None:
        try:
            outcome["result"] = provider._predict_in_subprocess(image)
        except Exception as exc:
            outcome["error"] = exc

    caller = threading.Thread(target=run, daemon=True)
    caller.start()
    caller.join(_CALL_DEADLINE_SECONDS)
    assert not caller.is_alive(), "OCR call hung after a hard worker death"
    if "error" in outcome:
        raise outcome["error"]
    return outcome["result"]


@pytest.fixture
def clean_pool() -> Any:
    shutdown_ocr_pool()
    yield
    shutdown_ocr_pool()


@pytest.mark.usefixtures("clean_pool")
def test_hard_worker_death_falls_back_and_next_call_gets_a_fresh_pool(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    provider = PaddleOcrProvider()
    image = np.zeros((2, 2, 3), np.uint8)

    monkeypatch.setattr(paddle_provider, "_paddle_ocr_predict", _die_hard)
    with caplog.at_level("WARNING", logger=paddle_provider.logger.name):
        assert _call_with_deadline(provider, image) == []
    assert "PaddleOCR subprocess failed" in caplog.text
    assert paddle_provider._ocr_pool is None

    monkeypatch.setattr(paddle_provider, "_paddle_ocr_predict", _return_one_line)
    assert _call_with_deadline(provider, image) == [
        {"rec_texts": ["ok"], "rec_scores": [0.9]}
    ]
    assert paddle_provider._ocr_pool is not None
