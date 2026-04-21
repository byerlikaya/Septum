from __future__ import annotations

"""Tests for vector store RRF merge and retrieval-related helpers."""


from septum_api.routers.chat import _effective_top_k
from septum_api.services.vector_store import merge_rrf_result_lists


class TestMergeRrfResultLists:
    """Tests for merge_rrf_result_lists."""

    def test_returns_empty_when_top_k_zero(self) -> None:
        assert merge_rrf_result_lists([[(1, 0.9), (2, 0.8)]], top_k=0) == []

    def test_returns_empty_when_no_lists(self) -> None:
        assert merge_rrf_result_lists([], top_k=5) == []

    def test_single_list_preserves_order(self) -> None:
        results = [(10, 0.9), (20, 0.8), (30, 0.7)]
        merged = merge_rrf_result_lists([results], top_k=3)
        assert [cid for cid, _ in merged] == [10, 20, 30]

    def test_single_list_truncates_to_top_k(self) -> None:
        results = [(1, 0.9), (2, 0.8), (3, 0.7), (4, 0.6)]
        merged = merge_rrf_result_lists([results], top_k=2)
        assert len(merged) == 2
        assert [cid for cid, _ in merged] == [1, 2]

    def test_two_lists_merge_by_rrf_score(self) -> None:
        list_a = [(1, 0.9), (2, 0.8)]
        list_b = [(2, 0.95), (1, 0.85)]
        merged = merge_rrf_result_lists([list_a, list_b], top_k=2, rrf_k=60)
        assert len(merged) == 2
        chunk_ids = [cid for cid, _ in merged]
        assert 1 in chunk_ids and 2 in chunk_ids
        assert merged[0][1] >= merged[1][1]

    def test_two_lists_chunk_in_both_ranks_higher(self) -> None:
        list_a = [(1, 0.9), (2, 0.8)]
        list_b = [(2, 0.9), (1, 0.8)]
        merged = merge_rrf_result_lists([list_a, list_b], top_k=2, rrf_k=60)
        scores = {cid: score for cid, score in merged}
        assert scores[1] == scores[2]

    def test_three_lists_merge(self) -> None:
        list_a = [(1, 0.9)]
        list_b = [(2, 0.9)]
        list_c = [(1, 0.9), (2, 0.8)]
        merged = merge_rrf_result_lists([list_a, list_b, list_c], top_k=2, rrf_k=60)
        assert len(merged) == 2
        chunk_ids = [cid for cid, _ in merged]
        assert set(chunk_ids) == {1, 2}


class TestEffectiveTopK:
    """Tests for _effective_top_k (adaptive retrieval size)."""

    def test_chunk_count_zero_returns_base(self) -> None:
        assert _effective_top_k(5, 0) == 5

    def test_small_document_unchanged(self) -> None:
        assert _effective_top_k(5, 1) == 5
        assert _effective_top_k(5, 10) == 5

    def test_large_document_increases(self) -> None:
        assert _effective_top_k(5, 11) == 8
        assert _effective_top_k(5, 15) == 10
        assert _effective_top_k(5, 30) == 15

    def test_capped_at_max(self) -> None:
        assert _effective_top_k(5, 100) == 15
        assert _effective_top_k(20, 50) == 25

    def test_bonus_never_exceeds_10(self) -> None:
        assert _effective_top_k(5, 60) == 15
        assert _effective_top_k(10, 40) == 20
