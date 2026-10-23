"""Short untrusted text reaches NER through the chat query path and the PII gate."""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest
from septum_core.config import SeptumCoreConfig
from septum_core.detector import Detector

from septum_api.routers import chat as chat_module
from septum_api.services import pii_gate
from septum_api.services.anonymization_map import AnonymizationMap

SHORT_TEXTS = (
    ("Ahmet Yılmaz kim?", "tr", "Ahmet Yılmaz"),
    ("Ali Veli", "tr", "Ali Veli"),
    ("张伟在哪", "zh", "张伟"),
    ("张伟", "zh", "张伟"),
    ("김민준 어디?", "ko", "김민준"),
)


class _RecordingNER:
    """Tags ``name`` wherever it appears and records every text it is given."""

    def __init__(self, name: str) -> None:
        self._name = name
        self.texts: list[str] = []

    def __call__(self, text: str) -> list[dict]:
        self.texts.append(text)
        return [
            {"entity_group": "PER", "score": 0.99, "start": m.start(), "end": m.end()}
            for m in re.finditer(re.escape(self._name), text)
        ]


class _Registry:
    def __init__(self, ner: _RecordingNER) -> None:
        self._ner = ner

    def get_pipelines(self, language: str) -> list[_RecordingNER]:
        return [self._ner]


def _detector(name: str) -> tuple[Detector, _RecordingNER]:
    ner = _RecordingNER(name)
    detector = Detector(
        SeptumCoreConfig(use_presidio_layer=False, use_ner_layer=True),
        ner_registry=_Registry(ner),
    )
    return detector, ner


@pytest.mark.parametrize("text, language, name", SHORT_TEXTS)
def test_gate_masks_a_name_in_short_text(text: str, language: str, name: str) -> None:
    detector, ner = _detector(name)
    anon_map = AnonymizationMap(document_id=0, language=language)

    masked = pii_gate._gate_one(text, anon_map, detector, language)

    assert ner.texts
    assert name not in masked
    assert name in anon_map.entity_map


@pytest.mark.parametrize("text", (" ", "\n\t"))
def test_gate_does_not_call_ner_on_blank_text(text: str) -> None:
    detector, ner = _detector("x")

    pii_gate._gate_one(text, AnonymizationMap(document_id=0, language="en"), detector, "en")

    assert ner.texts == []


@pytest.mark.asyncio
@pytest.mark.parametrize("text, language, name", SHORT_TEXTS)
async def test_chat_query_masks_a_name_in_short_text(
    text: str, language: str, name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    detector, ner = _detector(name)

    async def _create_sanitizer(db, settings, enable_ollama=False):
        return detector

    monkeypatch.setattr(chat_module, "create_sanitizer", _create_sanitizer)
    anon_map = AnonymizationMap(document_id=0, language=language)

    masked, count, _ = await chat_module._sanitize_query(text, language, None, anon_map, None)

    assert ner.texts
    assert count == 1
    assert name not in masked


def _column(index: int, technical: str, semantic: str | None, numeric: bool = False):
    return SimpleNamespace(
        index=index, technical_label=technical, semantic_label=semantic, is_numeric=numeric
    )


class _SchemaDb:
    """Answers the schema query with a fixed set of columns."""

    def __init__(self, columns: list[SimpleNamespace]) -> None:
        self._schema = SimpleNamespace(columns=columns)

    async def execute(self, _statement):
        return SimpleNamespace(scalar_one_or_none=lambda: self._schema)


async def _describe(columns: list[SimpleNamespace]) -> list[str]:
    return await chat_module._schema_column_descriptions(
        _SchemaDb(columns), 1, AnonymizationMap(document_id=1, language="en"), None
    )


@pytest.mark.asyncio
async def test_schema_descriptions_gate_only_semantic_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gated: list[list[str]] = []

    async def _gate(texts, anon_map, db, settings):
        gated.append(list(texts))
        return [f"<{t}>" if t else t for t in texts]

    monkeypatch.setattr(chat_module, "gate_untrusted_texts", _gate)

    descriptions = await _describe(
        [
            _column(0, "COLUMN_1", None),
            _column(1, "COLUMN_2", "Ali Veli", numeric=True),
            _column(2, "COLUMN_3", ""),
            _column(3, "COLUMN_4", "City"),
            _column(4, "Paid to Ali", None),
        ]
    )

    assert gated == [["", "Ali Veli", "", "City", ""]]
    assert descriptions == [
        "index 0 → COLUMN_1",
        "index 1 → <Ali Veli> (numeric)",
        "index 2 → COLUMN_3",
        "index 3 → <City>",
        "index 4 → COLUMN_5",
    ]


@pytest.mark.asyncio
async def test_schema_descriptions_without_semantic_labels_build_no_sanitizer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _no_sanitizer(*_args, **_kwargs):
        raise AssertionError("create_sanitizer must not run without a semantic label")

    monkeypatch.setattr(pii_gate, "create_sanitizer", _no_sanitizer)

    descriptions = await _describe(
        [_column(0, "COLUMN_1", None), _column(1, "Paid to Ali", "", numeric=True)]
    )

    assert descriptions == ["index 0 → COLUMN_1", "index 1 → COLUMN_2 (numeric)"]
