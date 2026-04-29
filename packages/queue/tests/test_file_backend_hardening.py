from __future__ import annotations

"""Regression tests for the hardening pass on the file backend."""

import json
from pathlib import Path

import pytest

from septum_queue import QueueError
from septum_queue.file_backend import FileQueueBackend


def test_topic_with_path_traversal_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(QueueError, match="invalid topic"):
        FileQueueBackend(tmp_path, topic="../escape")


def test_topic_with_absolute_path_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(QueueError, match="invalid topic"):
        FileQueueBackend(tmp_path, topic="/etc/septum")


def test_constructor_recovers_orphans_from_processing(tmp_path: Path) -> None:
    """A crash that left a payload in processing/ should be reclaimed."""
    topic_root = tmp_path / "septum-test"
    processing = topic_root / "processing"
    processing.mkdir(parents=True)
    incoming = topic_root / "incoming"
    incoming.mkdir(parents=True)

    orphan = processing / "00000000000000000001-orphan.json"
    orphan.write_text(json.dumps({"hello": "world"}), encoding="utf-8")

    FileQueueBackend(tmp_path, topic="septum-test")

    assert not orphan.exists()
    recovered = list(incoming.iterdir())
    assert len(recovered) == 1
    assert recovered[0].name == "00000000000000000001-orphan.json"


@pytest.mark.asyncio
async def test_corrupted_payload_routes_to_dead_letter(tmp_path: Path) -> None:
    backend = FileQueueBackend(tmp_path, topic="dl-topic")

    # Skip the publish path's atomic write so the bad bytes survive.
    bad_name = "00000000000000000001-bad.json"
    (tmp_path / "dl-topic" / "incoming" / bad_name).write_text(
        "not json {[", encoding="utf-8"
    )

    consumed = backend.consume(batch_size=1, block_ms=0)
    with pytest.raises(QueueError, match="dead-letter"):
        async for _ in consumed:
            pass

    dead = list((tmp_path / "dl-topic" / "dead-letter").iterdir())
    assert [p.name for p in dead] == [bad_name]


@pytest.mark.asyncio
async def test_nack_requeue_mints_fresh_filename(tmp_path: Path) -> None:
    backend = FileQueueBackend(tmp_path, topic="rq")
    original = await backend.publish({"x": 1})

    async for message in backend.consume(batch_size=1, block_ms=0):
        await backend.nack(message.id, requeue=True)
        break

    incoming_names = sorted(
        p.name for p in (tmp_path / "rq" / "incoming").iterdir()
    )
    # Old name must be gone (so a poison message does not starve newer
    # entries by lex-sorting first), and exactly one fresh entry must
    # have replaced it.
    assert original not in incoming_names
    assert len(incoming_names) == 1
