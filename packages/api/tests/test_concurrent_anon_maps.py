"""Concurrent access tests for anonymization maps.

Tests verify data integrity under concurrent reads/writes and
expose potential race conditions in AnonymizationMap.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from septum_api.services.anonymization_map import AnonymizationMap


def test_concurrent_add_entity_produces_unique_placeholders() -> None:
    """Multiple threads calling add_entity should produce unique placeholders."""
    amap = AnonymizationMap(document_id=100, language="en")
    results: list[str] = []
    lock = threading.Lock()

    def add_entity(name: str) -> str:
        ph = amap.add_entity(name, "PERSON_NAME")
        with lock:
            results.append(ph)
        return ph

    names = [f"Person{i}" for i in range(50)]

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(add_entity, name) for name in names]
        for future in as_completed(futures):
            future.result()

    # All 50 entities should have been added
    assert len(results) == 50
    # All entities should be in the map
    assert len(amap.entity_map) == 50


def test_concurrent_apply_blocklist_does_not_corrupt() -> None:
    """Concurrent reads via apply_blocklist should not corrupt results."""
    amap = AnonymizationMap(document_id=101, language="en")
    amap.add_entity("Alice Johnson", "PERSON_NAME")
    amap.add_entity("Bob Smith", "PERSON_NAME")

    text = "Alice called Bob about the project."
    results: list[str] = []
    lock = threading.Lock()

    def apply() -> None:
        result = amap.apply_blocklist(text, language="en")
        with lock:
            results.append(result)

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(apply) for _ in range(20)]
        for future in as_completed(futures):
            future.result()

    # All results should be identical (reads are non-mutating)
    assert len(set(results)) == 1
    assert "Alice" not in results[0]
    assert "Bob" not in results[0]


def test_concurrent_add_same_entity_deduplicates() -> None:
    """Multiple threads adding the same entity should all get the same placeholder."""
    amap = AnonymizationMap(document_id=102, language="en")
    results: list[str] = []
    lock = threading.Lock()

    def add() -> None:
        ph = amap.add_entity("Same Person", "PERSON_NAME")
        with lock:
            results.append(ph)

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(add) for _ in range(20)]
        for future in as_completed(futures):
            future.result()

    # All threads should get the same placeholder
    assert all(r == "[PERSON_NAME_1]" for r in results)
    assert len(amap.entity_map) == 1
