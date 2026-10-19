"""NER pipelines load only from a verified local snapshot, with no remote code."""

from __future__ import annotations

from pathlib import Path

import pytest
from septum_core import ner_model_registry as registry_module
from septum_core.model_integrity import ModelIntegrityError, UnpinnedModelError
from septum_core.ner_model_registry import NERModelRegistry


@pytest.fixture
def loads(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []

    def _pipeline(task, **kwargs):
        calls.append({"task": task, **kwargs})
        return object()

    monkeypatch.setattr(registry_module, "pipeline", _pipeline)
    monkeypatch.setattr(registry_module, "get_device", lambda: "cpu")
    return calls


def test_pipeline_gets_the_verified_dir_without_remote_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, loads: list[dict]
) -> None:
    requested: list[str] = []
    monkeypatch.setattr(
        registry_module,
        "ensure_model_snapshot",
        lambda spec: requested.append(spec) or tmp_path,
    )
    registry = NERModelRegistry()

    registry.get_pipelines("en")

    assert requested == registry.DEFAULT_MODEL_MAP["en"]
    [call] = loads
    assert call["model"] == str(tmp_path)
    assert call["tokenizer"] == str(tmp_path)
    assert call["trust_remote_code"] is False
    assert call["model_kwargs"] == {"use_safetensors": True}


def test_unpinned_override_is_refused_before_any_load(loads: list[dict]) -> None:
    registry = NERModelRegistry(_overrides={"en": "someone/unpinned-ner"})

    with pytest.raises(UnpinnedModelError):
        registry.get_pipelines("en")

    assert loads == []


def test_integrity_failure_propagates_and_nothing_is_cached(
    monkeypatch: pytest.MonkeyPatch, loads: list[dict]
) -> None:
    def _refuse(spec: str) -> Path:
        raise ModelIntegrityError(spec, "file 'model.safetensors' does not match its pinned hash")

    monkeypatch.setattr(registry_module, "ensure_model_snapshot", _refuse)
    registry = NERModelRegistry()

    with pytest.raises(ModelIntegrityError):
        registry.get_pipelines("en")

    assert loads == []
    assert registry._loaded_models == {}


@pytest.mark.parametrize("language", sorted(NERModelRegistry().DEFAULT_MODEL_MAP))
def test_every_default_model_is_in_the_manifest(language: str) -> None:
    from septum_core.model_pins import MODEL_PINS

    for model_id in NERModelRegistry().DEFAULT_MODEL_MAP[language]:
        assert model_id in MODEL_PINS, model_id


def test_every_suggested_preset_is_in_the_manifest() -> None:
    from septum_core.model_pins import MODEL_PINS

    suggested = NERModelRegistry().SUGGESTED_MODELS
    ids = [entry["model_id"] for entries in suggested.values() for entry in entries]

    assert [m for m in ids if m not in MODEL_PINS] == []
