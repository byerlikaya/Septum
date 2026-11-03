"""Refusals are detected by a sentinel the prompt asks for, not by phrases.

A placeholder question whose answer names no placeholder is replaced by
the context placeholders only when the model answered with the
``NO_ANSWER_SENTINEL`` (or a long bullet list). Wording in any language,
such as "not found" inside a real answer, no longer counts as a refusal,
and the sentinel itself never reaches the user.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from septum_api.routers.chat import _is_unhelpful_answer, _shape_masked_answer
from septum_api.services.prompts import PromptCatalog

SENTINEL = PromptCatalog.NO_ANSWER_SENTINEL
CONTEXT = ["[PERSON_NAME_1]", "[PERSON_NAME_2]"]
LISTED = "- [PERSON_NAME_1]\n- [PERSON_NAME_2]"


def _turn(query_has_placeholder: bool = True) -> SimpleNamespace:
    return SimpleNamespace(query_placeholders=[], query_has_placeholder=query_has_placeholder)


@pytest.mark.parametrize("answer", [SENTINEL, f" {SENTINEL}\n", f"Sorry. {SENTINEL}"])
def test_sentinel_answer_is_replaced_by_the_context_placeholders(answer: str) -> None:
    assert _shape_masked_answer(answer, _turn(), CONTEXT) == LISTED


@pytest.mark.parametrize(
    "answer",
    [
        "The contract was not found in the archive until 2021.",
        "Sözleşmede bu bilgi bulunamadı.",
        "Diese Information ist im Dokument nicht enthalten.",
        "I cannot find that information in the document.",
    ],
)
def test_wording_alone_is_not_a_refusal(answer: str) -> None:
    assert not _is_unhelpful_answer(answer)
    assert _shape_masked_answer(answer, _turn(), CONTEXT) == answer


def test_long_bullet_list_is_still_unhelpful() -> None:
    answer = "\n".join(f"- item {i}" for i in range(5))
    assert _shape_masked_answer(answer, _turn(), CONTEXT) == LISTED


def test_sentinel_is_removed_outside_the_placeholder_branch() -> None:
    shaped = _shape_masked_answer(f"[PERSON_NAME_1] {SENTINEL}", _turn(), CONTEXT)
    assert shaped == "[PERSON_NAME_1]"
    assert SENTINEL not in _shape_masked_answer(f"Answer. {SENTINEL}", _turn(False), CONTEXT)


def test_bare_sentinel_without_a_list_falls_back_to_the_standard_refusal() -> None:
    assert _shape_masked_answer(SENTINEL, _turn(False), CONTEXT) == PromptCatalog.NO_ANSWER_TEXT


def test_placeholder_instruction_asks_for_the_sentinel() -> None:
    instruction = PromptCatalog.placeholder_list_instruction(CONTEXT)
    assert SENTINEL in instruction
    assert "Do not refuse" not in instruction


def test_phrase_list_is_gone() -> None:
    assert not hasattr(PromptCatalog, "REFUSAL_PHRASES")
