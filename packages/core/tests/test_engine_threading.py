from __future__ import annotations

"""Concurrency regression tests for :class:`SeptumEngine`.

The engine is called from multiple threads (FastAPI worker pool, MCP
streamable-http transport). Without the internal lock, the eviction
path could race the registration path and either raise
``RuntimeError: dictionary changed size during iteration`` or leak an
entry whose expiry was already deleted, leaving raw PII in memory
until process death.
"""

import threading

import pytest

from septum_core import SeptumCoreConfig, SeptumEngine
from septum_core.ports import NullSemanticDetectionPort


@pytest.fixture
def engine() -> SeptumEngine:
    config = SeptumCoreConfig(use_presidio_layer=True, use_ner_layer=False)
    return SeptumEngine(
        regulations=["gdpr"],
        config=config,
        semantic_port=NullSemanticDetectionPort(),
    )


def test_concurrent_mask_and_release_does_not_raise(engine: SeptumEngine) -> None:
    """Run mask + release from many threads to exercise the session lock."""

    errors: list[BaseException] = []
    barrier = threading.Barrier(8)

    def worker(idx: int) -> None:
        try:
            barrier.wait(timeout=2)
            for i in range(50):
                result = engine.mask(
                    f"Email user{idx}_{i}@example.com please.", language="en"
                )
                # Read while another thread might be evicting/registering.
                _ = engine.get_session_map(result.session_id)
                _ = engine.active_session_count()
                engine.release(result.session_id)
        except BaseException as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, f"Concurrent engine ops raised: {errors!r}"
    # All sessions explicitly released; eviction path should leave the
    # dicts empty without raising.
    assert engine.active_session_count() == 0
