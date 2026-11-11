"""The leak invariant: a chunk may never expose a value the whole-document view masks."""

from __future__ import annotations

from tests.benchmark_retrieval import _ChunkedLeak, _score_value
from tests.retrieval_metrics import alignment

VALUE = "Marta Quinn"
TEXT = f"The clerk wrote down {VALUE} before the long meeting ended. " * 2
START = TEXT.index(VALUE)
END = START + len(VALUE)


def _leak(sanitized: str) -> _ChunkedLeak:
    span = (0, len(TEXT))
    return _ChunkedLeak([TEXT], [span], [sanitized], [span])


def _view(masked: str) -> tuple[int, str, str]:
    return 0, TEXT, masked


def _score(chunk_masked: str, document_masked: str):
    return _score_value(_leak(chunk_masked), "PERSON_NAME", VALUE, START, END, _view(document_masked))


def test_chunk_exposing_a_value_the_document_masks_is_a_violation() -> None:
    masked = TEXT.replace(VALUE, "[PERSON_NAME_1]")
    record = _score(TEXT, masked)
    assert record is not None
    assert record.literal and record.violation


def test_consistent_masking_is_not_a_violation() -> None:
    masked = TEXT.replace(VALUE, "[PERSON_NAME_1]")
    record = _score(masked, masked)
    assert record is not None
    assert not record.literal and not record.fragment and not record.violation


def test_value_the_document_also_leaves_raw_is_not_a_violation() -> None:
    record = _score(TEXT, TEXT)
    assert record is not None
    assert record.literal and not record.violation


def test_other_occurrence_in_the_chunk_is_not_counted() -> None:
    second = TEXT.rindex(VALUE)
    assert second != START
    masked = TEXT[:second].replace(VALUE, "[PERSON_NAME_1]") + TEXT[second:]
    assert alignment(TEXT, masked)
    record = _score(masked, masked)
    assert record is not None
    assert not record.literal and not record.violation


def test_chunk_edge_inside_the_value_with_full_placeholder_is_not_a_violation() -> None:
    cut = START + 5
    raw = TEXT[:cut]
    masked = raw[:START] + "[PERSON_NAME_1]"
    leak = _ChunkedLeak([raw], [(0, cut)], [masked], [(0, cut)])
    document_masked = TEXT.replace(VALUE, "[PERSON_NAME_1]")
    record = _score_value(leak, "PERSON_NAME", VALUE, START, END, _view(document_masked))
    assert record is not None
    assert not record.literal and not record.fragment and not record.violation


def test_window_edge_inside_the_value_with_full_placeholder_is_not_a_violation() -> None:
    cut = START + 5
    masked_chunk = TEXT.replace(VALUE, "[PERSON_NAME_1]")
    window_masked = TEXT[:START] + "[PERSON_NAME_1]"
    record = _score_value(_leak(masked_chunk), "PERSON_NAME", VALUE, START, END, (0, TEXT[:cut], window_masked))
    assert record is not None
    assert not record.literal and not record.violation


def test_window_edge_inside_the_value_still_flags_a_chunk_that_exposes_it() -> None:
    cut = START + 5
    window_masked = TEXT[:START] + "[PERSON_NAME_1]"
    record = _score_value(_leak(TEXT), "PERSON_NAME", VALUE, START, END, (0, TEXT[:cut], window_masked))
    assert record is not None
    assert record.literal and record.violation
