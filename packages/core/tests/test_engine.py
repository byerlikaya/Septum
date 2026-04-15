from __future__ import annotations

"""End-to-end tests for :class:`septum_core.engine.SeptumEngine`.

Exercises the public facade: ``mask()`` → stash session map → simulate
an LLM reply that still carries placeholders → ``unmask()`` restores
the originals. Runs entirely offline with the NER layer disabled so
the tests don't try to download a transformer model.
"""

import pytest

from septum_core import AnonymizationMap, SeptumCoreConfig, SeptumEngine
from septum_core.ports import NullSemanticDetectionPort


@pytest.fixture
def engine() -> SeptumEngine:
    config = SeptumCoreConfig(use_presidio_layer=True, use_ner_layer=False)
    return SeptumEngine(
        regulations=["gdpr", "kvkk"],
        config=config,
        semantic_port=NullSemanticDetectionPort(),
    )


def test_mask_generates_stable_session_id(engine: SeptumEngine) -> None:
    result = engine.mask("Contact: jane@example.com.", language="en")

    assert result.session_id
    assert len(result.session_id) >= 16
    assert "[EMAIL_ADDRESS_1]" in result.masked_text
    assert "jane@example.com" not in result.masked_text


def test_mask_reports_entity_counts(engine: SeptumEngine) -> None:
    result = engine.mask(
        "Email jane@example.com and pay the invoice.", language="en"
    )

    assert result.entity_count == 1
    assert result.entity_type_counts == {"EMAIL_ADDRESS": 1}
    assert len(result.detected_spans) == 1


def test_unmask_round_trips_llm_response(engine: SeptumEngine) -> None:
    result = engine.mask(
        "Please follow up with jane@example.com soon.", language="en"
    )
    llm_reply = f"Reminder sent to {('[EMAIL_ADDRESS_1]')} successfully."

    restored = engine.unmask(llm_reply, result.session_id)

    assert restored == "Reminder sent to jane@example.com successfully."


def test_unmask_returns_input_when_session_unknown(engine: SeptumEngine) -> None:
    text = "[EMAIL_ADDRESS_1] is unchanged"
    assert engine.unmask(text, session_id="nonexistent") == text


def test_release_drops_session(engine: SeptumEngine) -> None:
    result = engine.mask("Contact jane@example.com.", language="en")
    engine.release(result.session_id)

    llm_reply = "Reply to [EMAIL_ADDRESS_1]."
    assert engine.unmask(llm_reply, result.session_id) == llm_reply


def test_mask_with_empty_regulations_still_runs_baseline_recognizers() -> None:
    config = SeptumCoreConfig(use_presidio_layer=True, use_ner_layer=False)
    engine = SeptumEngine(
        regulations=[],
        config=config,
        semantic_port=NullSemanticDetectionPort(),
    )
    result = engine.mask("Contact jane@example.com please.", language="en")
    assert "[EMAIL_ADDRESS_1]" in result.masked_text


def test_multiple_sessions_are_independent(engine: SeptumEngine) -> None:
    first = engine.mask("Email first@example.com soon.", language="en")
    second = engine.mask("Email second@example.com later.", language="en")
    assert first.session_id != second.session_id

    restored_first = engine.unmask("Reply to [EMAIL_ADDRESS_1].", first.session_id)
    restored_second = engine.unmask("Reply to [EMAIL_ADDRESS_1].", second.session_id)

    assert restored_first == "Reply to first@example.com."
    assert restored_second == "Reply to second@example.com."
