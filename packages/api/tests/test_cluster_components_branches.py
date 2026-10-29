"""Characterization of the connected-components helper edge cases (pins behaviour before a refactor)."""

from __future__ import annotations

from septum_api.services.document_cluster_service import _connected_components


def _normalised(components: list[set[int]]) -> list[list[int]]:
    return sorted(sorted(component) for component in components)


def test_no_nodes_yields_no_components() -> None:
    assert _connected_components([], []) == []


def test_nodes_without_edges_are_singletons() -> None:
    assert _normalised(_connected_components([1, 2, 3], [])) == [[1], [2], [3]]


def test_self_loops_and_edges_to_unknown_nodes_are_ignored() -> None:
    components = _connected_components([1, 2, 3], [(1, 1), (2, 9), (9, 3)])

    assert _normalised(components) == [[1], [2], [3]]


def test_a_chain_collapses_into_one_component() -> None:
    components = _connected_components([1, 2, 3, 4], [(1, 2), (2, 3)])

    assert _normalised(components) == [[1, 2, 3], [4]]


def test_edge_direction_and_duplicates_do_not_matter() -> None:
    components = _connected_components([1, 2, 3, 4], [(2, 1), (1, 2), (2, 1), (4, 3)])

    assert _normalised(components) == [[1, 2], [3, 4]]
