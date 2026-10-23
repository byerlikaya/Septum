"""NER pipelines load only from a verified local snapshot, with no remote code."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from ner_fakes import FakePipeline
from septum_core import model_integrity
from septum_core import ner_model_registry as registry_module
from septum_core.detector import _NER_MIN_SPAN_LEN_PER_LANGUAGE, Detector
from septum_core.language_resolution import script_min_span_length
from septum_core.model_integrity import ModelIntegrityError, UnpinnedModelError
from septum_core.model_pins import MODEL_PINS
from septum_core.ner_model_registry import (
    MODEL_LABEL_MAPS,
    NERModelRegistry,
    _WindowedNERPipeline,
)
from septum_core.ner_windowing import NERWindowingError

HRL = "Davlan/bert-base-multilingual-cased-ner-hrl"
WIKIANN = "Davlan/xlm-roberta-base-wikiann-ner"
ERNIE = "gyr66/Ernie-3.0-base-chinese-finetuned-ner"
VERY_LARGE = 10**30
LOGGER = "septum_core.ner_model_registry"


def _model_dir(root: Path, spec: str) -> Path:
    return root / spec.replace("/", "--").replace("@", "__")


@pytest.fixture
def loads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Every pipeline and snapshot the registry asks for, with a factory the test can replace."""
    env = SimpleNamespace(calls=[], requested=[], factory=lambda kwargs: FakePipeline())

    def _pipeline(task, **kwargs):
        env.calls.append({"task": task, **kwargs})
        return env.factory(kwargs)

    def _snapshot(spec: str) -> Path:
        env.requested.append(spec)
        return _model_dir(tmp_path, spec)

    monkeypatch.setattr(registry_module, "pipeline", _pipeline)
    monkeypatch.setattr(registry_module, "get_device", lambda: "cpu")
    monkeypatch.setattr(registry_module, "ensure_model_snapshot", _snapshot)
    return env


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]


def test_pipeline_gets_the_verified_dir_without_remote_code(tmp_path: Path, loads) -> None:
    registry = NERModelRegistry()

    registry.get_pipelines("en")

    assert loads.requested == registry.DEFAULT_MODEL_MAP["en"]
    [call] = loads.calls
    assert call["model"] == str(_model_dir(tmp_path, WIKIANN))
    assert call["tokenizer"] == call["model"]
    assert call["trust_remote_code"] is False
    assert call["model_kwargs"] == {"use_safetensors": True}


def test_unpinned_override_is_refused_before_any_load(monkeypatch: pytest.MonkeyPatch, loads) -> None:
    monkeypatch.setattr(registry_module, "ensure_model_snapshot", model_integrity.ensure_model_snapshot)
    registry = NERModelRegistry(_overrides={"en": "someone/unpinned-ner"})

    with pytest.raises(UnpinnedModelError):
        registry.get_pipelines("en")

    assert loads.calls == []


def test_integrity_failure_propagates_and_nothing_is_cached(monkeypatch: pytest.MonkeyPatch, loads) -> None:
    def _refuse(spec: str) -> Path:
        raise ModelIntegrityError(spec, "file 'model.safetensors' does not match its pinned hash")

    monkeypatch.setattr(registry_module, "ensure_model_snapshot", _refuse)
    registry = NERModelRegistry()

    with pytest.raises(ModelIntegrityError):
        registry.get_pipelines("en")

    assert loads.calls == []
    assert registry._loaded_models == {}


@pytest.mark.parametrize("language", sorted(NERModelRegistry().DEFAULT_MODEL_MAP))
def test_every_default_model_is_in_the_manifest(language: str) -> None:
    for model_id in NERModelRegistry().DEFAULT_MODEL_MAP[language]:
        assert model_id in MODEL_PINS, model_id


def test_every_suggested_preset_is_in_the_manifest() -> None:
    suggested = NERModelRegistry().SUGGESTED_MODELS
    ids = [entry["model_id"] for entries in suggested.values() for entry in entries]

    assert [m for m in ids if m not in MODEL_PINS] == []


def test_wikineural_is_not_used_anywhere_in_production() -> None:
    registry = NERModelRegistry()
    used = [m for models in registry.DEFAULT_MODEL_MAP.values() for m in models]
    presets = [e["model_id"] for entries in registry.SUGGESTED_MODELS.values() for e in entries]

    assert not [m for m in used + presets if "wikineural" in m.lower()]
    assert "Babelscape/wikineural-multilingual-ner" not in MODEL_PINS


def test_hrl_is_the_zh_default_and_the_general_fallback() -> None:
    registry = NERModelRegistry()

    assert registry.DEFAULT_MODEL_MAP["zh"] == [HRL]
    assert registry.DEFAULT_MODEL_MAP["fallback"] == [HRL]


@pytest.mark.parametrize("language", ["de", "it", "nl", "pl", "ru", "ko"])
def test_hrl_covers_the_languages_the_removed_model_was_assigned(language: str) -> None:
    assert NERModelRegistry().DEFAULT_MODEL_MAP[language] == [HRL]


def test_thai_keeps_the_only_model_with_thai_recall() -> None:
    assert NERModelRegistry().DEFAULT_MODEL_MAP["th"] == [WIKIANN]


def test_ja_and_zh_share_one_model_and_one_cjk_span_floor() -> None:
    registry = NERModelRegistry()

    assert registry.DEFAULT_MODEL_MAP["ja"] == registry.DEFAULT_MODEL_MAP["zh"]
    assert _NER_MIN_SPAN_LEN_PER_LANGUAGE["ja"] == _NER_MIN_SPAN_LEN_PER_LANGUAGE["zh"] == 2
    assert script_min_span_length("血圧計の電池") == _NER_MIN_SPAN_LEN_PER_LANGUAGE["ja"]
    assert script_min_span_length("李明") == _NER_MIN_SPAN_LEN_PER_LANGUAGE["zh"]


def test_ernie_is_an_opt_in_preset_and_never_a_default() -> None:
    registry = NERModelRegistry()
    used = [m for models in registry.DEFAULT_MODEL_MAP.values() for m in models]
    ernie = [e for e in registry.SUGGESTED_MODELS["zh"] if e["model_id"] == ERNIE]

    assert ERNIE not in used
    assert len(ernie) == 1
    assert "opt-in" in ernie[0]["label"] and "licence" in ernie[0]["label"].lower()


def test_every_label_map_belongs_to_a_pinned_model() -> None:
    assert set(MODEL_LABEL_MAPS) <= set(MODEL_PINS)


def test_pinned_revisions_of_the_new_models() -> None:
    assert MODEL_PINS[HRL].revision == "e756de7f7b8f64fea0c3d7c3872f1322fab747b1"
    assert MODEL_PINS[ERNIE].revision == "3f3c3fa40cf87298586dbbed55fd7ca1988719a8"


@pytest.mark.parametrize("spec", [ERNIE, f"{ERNIE}@3f3c3fa40cf87298586dbbed55fd7ca1988719a8"])
def test_loader_applies_the_label_map_to_pinned_commit_specs(spec: str, loads) -> None:
    registry = NERModelRegistry(_overrides={"zh": spec})

    [pipe] = registry.get_pipelines("zh")

    assert isinstance(pipe, _WindowedNERPipeline)
    assert pipe._label_map is MODEL_LABEL_MAPS[ERNIE]


def test_ernie_email_maps_and_unknown_labels_are_dropped() -> None:
    text = "李明 北京市 阿里巴巴 教育部 组织 王者荣耀 经理 a@b.cn QQ"
    emit = {
        "李明": "name",
        "北京市": "address",
        "阿里巴巴": "company",
        "教育部": "government",
        "组织": "organization",
        "王者荣耀": "game",
        "经理": "position",
        "a@b.cn": "email",
        "QQ": "QQ",
    }
    pipe = _WindowedNERPipeline(
        FakePipeline(max_positions=514, model_max_length=512, emit=emit),
        MODEL_LABEL_MAPS[ERNIE],
    )

    spans = Detector._from_ner_results(pipe(text), text, "zh")

    by_text = {text[s.start:s.end]: s.entity_type for s in spans}
    assert by_text == {
        "李明": "PERSON_NAME",
        "北京市": "LOCATION",
        "阿里巴巴": "ORGANIZATION_NAME",
        "教育部": "ORGANIZATION_NAME",
        "组织": "ORGANIZATION_NAME",
        "a@b.cn": "EMAIL_ADDRESS",
    }


def test_unmapped_model_labels_are_reported_once_and_by_name_only(
    loads, caplog: pytest.LogCaptureFixture
) -> None:
    loads.factory = lambda kwargs: FakePipeline(id2label={0: "O", 1: "B-PER", 2: "B-FOO", 3: "I-FOO", 4: "B-DATE"})
    registry = NERModelRegistry()

    with caplog.at_level("WARNING", logger=LOGGER):
        registry.get_pipelines("en")
        registry.get_pipelines("en")

    assert _warnings(caplog) == [
        f"NER model {WIKIANN} emits labels with no entity mapping, ignored: FOO"
    ]


def test_unmapped_warning_is_emitted_once_per_model(
    loads, caplog: pytest.LogCaptureFixture
) -> None:
    loads.factory = lambda kwargs: FakePipeline(id2label={0: "O", 1: "B-FOO"})

    with caplog.at_level("WARNING", logger=LOGGER):
        NERModelRegistry().get_pipelines("en")
        NERModelRegistry().get_pipelines("zh")

    assert _warnings(caplog) == [
        f"NER model {WIKIANN} emits labels with no entity mapping, ignored: FOO",
        f"NER model {HRL} emits labels with no entity mapping, ignored: FOO",
    ]


def test_fully_mapped_model_emits_no_warning(loads, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING", logger=LOGGER):
        NERModelRegistry().get_pipelines("en")

    assert _warnings(caplog) == []


def test_a_mapped_model_is_judged_only_by_its_map() -> None:
    labels = {0: "O", 1: "B-name", 2: "B-game", 3: "B-position", 4: "B-address", 5: "B-QQ"}
    pipe = _WindowedNERPipeline(FakePipeline(id2label=labels), MODEL_LABEL_MAPS[ERNIE])

    assert pipe.unmapped_labels() == []


def test_a_mapped_model_with_unlisted_labels_emits_no_warning(loads, caplog: pytest.LogCaptureFixture) -> None:
    loads.factory = lambda kwargs: FakePipeline(id2label={0: "O", 1: "B-name", 2: "B-game"})

    with caplog.at_level("WARNING", logger=LOGGER):
        NERModelRegistry(_overrides={"zh": ERNIE}).get_pipelines("zh")

    assert _warnings(caplog) == []


def test_a_mapless_model_with_an_unknown_label_warns(loads, caplog: pytest.LogCaptureFixture) -> None:
    loads.factory = lambda kwargs: FakePipeline(id2label={0: "O", 1: "B-FOO"})

    with caplog.at_level("WARNING", logger=LOGGER):
        NERModelRegistry().get_pipelines("en")

    assert _warnings(caplog) == [f"NER model {WIKIANN} emits labels with no entity mapping, ignored: FOO"]


def test_preset_suffix_is_taken_from_the_stored_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        registry_module, "OPT_IN_MODELS", {"acme/ner": "reason text", "acme/bare": ""}
    )

    assert registry_module._preset_label("acme/ner", "Acme") == "Acme (opt-in: reason text)"
    assert registry_module._preset_label("acme/bare", "Acme") == "Acme (opt-in)"
    assert registry_module._preset_label("acme/plain", "Acme") == "Acme"


def test_unprefixed_and_bare_prefix_labels_are_reported_by_name() -> None:
    pipe = _WindowedNERPipeline(FakePipeline(id2label={1: "FOO", 2: "B-", 3: "B-PER"}), None)

    assert pipe.unmapped_labels() == ["B-", "FOO"]


def test_miscellaneous_labels_are_never_reported_as_unmapped() -> None:
    pipe = _WindowedNERPipeline(FakePipeline(id2label={1: "B-MISC", 2: "I-MISC", 3: "B-DATE"}), None)

    assert pipe.unmapped_labels() == []


def test_an_unloadable_small_budget_fails_at_load_and_is_not_cached(loads) -> None:
    loads.factory = lambda kwargs: FakePipeline(max_positions=4, model_max_length=4)
    registry = NERModelRegistry()

    with pytest.raises(NERWindowingError, match="too small"):
        registry.get_pipelines("en")

    assert registry._loaded_models == {}


def test_an_unlimited_model_without_position_embeddings_fails_at_load(loads) -> None:
    loads.factory = lambda kwargs: FakePipeline(model_max_length=VERY_LARGE, embeddings=False)
    registry = NERModelRegistry()

    with pytest.raises(NERWindowingError, match="cannot derive"):
        registry.get_pipelines("en")

    assert registry._loaded_models == {}
