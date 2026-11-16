"""Document erasure attempts every tier, so one failing tier never keeps the map.

A persistent failure of an earlier tier (an index file that cannot be
removed) used to stop the erasure before the anonymization map, and every
retry hit the same failure, so the map holding the originals was kept
indefinitely. Every tier now runs; the first failure is raised and any later
one is logged by document id, tier and error class only.
"""

from __future__ import annotations

import logging
from typing import Optional

import pytest
from septum_api.services import document_pipeline as dp
from septum_api.services.document_anon_store import DocumentMapErasureError

DOCUMENT_ID = 4242


class _Tiers:
    """Records which tiers ran and fails the ones named in ``failing``."""

    def __init__(self, failing: dict[str, BaseException]) -> None:
        self.failing = failing
        self.ran: list[str] = []

    def run(self, tier: str) -> None:
        self.ran.append(tier)
        error: Optional[BaseException] = self.failing.get(tier)
        if error is not None:
            raise error


@pytest.fixture
def tiers(monkeypatch: pytest.MonkeyPatch):
    def _install(**failing: BaseException) -> _Tiers:
        state = _Tiers(failing)

        class _Index:
            def __init__(self, tier: str) -> None:
                self.tier = tier

            def delete_index(self, document_id: int) -> None:
                assert document_id == DOCUMENT_ID
                state.run(self.tier)

        async def _pop(document_id: int) -> None:
            assert document_id == DOCUMENT_ID
            state.run("anonymization_map")

        async def _clear() -> None:
            state.run("detection_cache")

        monkeypatch.setattr(dp, "VectorStore", lambda: _Index("vector_index"))
        monkeypatch.setattr(dp, "BM25Retriever", lambda: _Index("bm25_index"))
        monkeypatch.setattr(dp, "pop_document_map", _pop)
        monkeypatch.setattr(dp, "clear_detection_cache", _clear)
        return state

    return _install


ALL_TIERS = ["vector_index", "bm25_index", "anonymization_map", "detection_cache"]


@pytest.mark.parametrize("failing_tier", ["vector_index", "bm25_index"])
async def test_a_failing_index_still_erases_the_map(tiers, failing_tier: str) -> None:
    error = PermissionError(13, "denied", "/secret/path")
    state = tiers(**{failing_tier: error})

    with pytest.raises(dp.ArtifactErasureError) as raised:
        await dp.erase_document_artifacts(DOCUMENT_ID)

    assert raised.value.tier == failing_tier
    assert raised.value.error is error
    assert raised.value.__cause__ is error
    assert state.ran == ALL_TIERS


async def test_a_failing_map_still_clears_the_reply_cache(tiers) -> None:
    state = tiers(anonymization_map=DocumentMapErasureError("disk"))

    with pytest.raises(dp.ArtifactErasureError) as raised:
        await dp.erase_document_artifacts(DOCUMENT_ID)

    assert raised.value.tier == "anonymization_map"
    assert state.ran == ALL_TIERS


async def test_a_failing_source_file_still_erases_every_other_tier(tiers, tmp_path) -> None:
    state = tiers()
    missing_parent = tmp_path / "not-a-dir"
    missing_parent.write_bytes(b"")

    with pytest.raises(dp.ArtifactErasureError) as raised:
        await dp.erase_document_artifacts(
            DOCUMENT_ID, source_path=str(missing_parent / "source.enc")
        )

    assert raised.value.tier == "source_file"
    assert state.ran == ALL_TIERS


async def test_the_reply_cache_tier_stays_last_and_optional(tiers) -> None:
    state = tiers(vector_index=OSError("io"))

    with pytest.raises(dp.ArtifactErasureError):
        await dp.erase_document_artifacts(DOCUMENT_ID, clear_reply_cache=False)

    assert state.ran == ALL_TIERS[:-1]


async def test_several_failures_raise_the_first_and_log_the_rest(
    tiers, caplog: pytest.LogCaptureFixture
) -> None:
    tiers(
        vector_index=PermissionError(13, "denied", "/secret/faiss"),
        bm25_index=IsADirectoryError(21, "is a directory", "/secret/bm25"),
        detection_cache=DocumentMapErasureError("disk"),
    )

    with caplog.at_level(logging.ERROR, logger=dp.logger.name):
        with pytest.raises(dp.ArtifactErasureError) as raised:
            await dp.erase_document_artifacts(DOCUMENT_ID)

    assert raised.value.tier == "vector_index"
    assert f"document_id={DOCUMENT_ID} tier=bm25_index error=IsADirectoryError" in caplog.text
    assert "tier=vector_index" not in caplog.text
    assert "/secret" not in caplog.text


async def test_an_unexpected_cache_error_still_reports_earlier_failures(
    tiers, caplog: pytest.LogCaptureFixture
) -> None:
    tiers(bm25_index=PermissionError(13, "denied", "/secret/bm25"), detection_cache=ValueError("x"))

    with caplog.at_level(logging.ERROR, logger=dp.logger.name):
        with pytest.raises(ValueError):
            await dp.erase_document_artifacts(DOCUMENT_ID)

    assert f"document_id={DOCUMENT_ID} tier=bm25_index error=PermissionError" in caplog.text
    assert "/secret" not in caplog.text


async def test_no_failure_raises_nothing(tiers) -> None:
    state = tiers()

    await dp.erase_document_artifacts(DOCUMENT_ID)

    assert state.ran == ALL_TIERS
