"""Tests for the operator script that generates and drift-checks the model pin manifest."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "pin_models.py"
COMMIT_A = "a" * 40
COMMIT_B = "b" * 40
HASH_1 = "1" * 64
HASH_2 = "2" * 64
HASH_3 = "3" * 64


@pytest.fixture(scope="module")
def pm() -> ModuleType:
    spec = importlib.util.spec_from_file_location("pin_models_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(spec.name, None)


@pytest.fixture
def real_manifest(pm: ModuleType) -> Path:
    return pm.MANIFEST


@pytest.fixture
def sandbox(pm: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the script at a throwaway copy of the manifest under a throwaway root."""
    target = tmp_path / "packages/core/septum_core/model_pins.py"
    target.parent.mkdir(parents=True)
    shutil.copy(pm.MANIFEST, target)
    monkeypatch.setattr(pm, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(pm, "MANIFEST", target)
    return target


def _model(repo: str, kind: str, commit: str, files: dict[str, str]) -> dict:
    return {
        "repo_id": repo,
        "kind": kind,
        "main": {
            "commit": commit,
            "files": [{"path": p, "sha256": h} for p, h in files.items()],
        },
    }


def _models() -> list[dict]:
    shared = {"config.json": HASH_1, "tokenizer.json": HASH_2}
    return [
        _model("org/one", "hf-transformers", COMMIT_A, {**shared, "model.safetensors": HASH_3}),
        _model("org/two", "hf-transformers", COMMIT_B, {**shared, "vocab.txt": HASH_1}),
        _model("org/three", "sentence-transformers", COMMIT_A, {**shared, "modules.json": HASH_2}),
        _model("org/four", "paddle", COMMIT_B, {"inference.json": HASH_3, "config.json": HASH_3}),
    ]


def _exec_source(source: str, tmp_path: Path) -> ModuleType:
    path = tmp_path / "rendered_pins.py"
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("rendered_pins", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


class TestManifestRendering:
    def test_constants_only_for_paths_used_by_three_or_more_pins(self, pm):
        names = pm._file_constants(_models())
        assert names == {"config.json": "_CONFIG_JSON", "tokenizer.json": "_TOKENIZER_JSON"}

    def test_constant_name_collision_is_rejected(self, pm):
        models = [
            _model(f"org/m{i}", "hf-transformers", COMMIT_A, {"a-b.json": HASH_1, "a_b.json": HASH_2})
            for i in range(3)
        ]
        with pytest.raises(RuntimeError, match="already taken"):
            pm._file_constants(models)

    def test_rendered_module_emits_constants_and_references_them(self, pm):
        source = pm._render_manifest(_models())
        assert pm.FILE_NAMES_MARKER in source
        assert '_CONFIG_JSON = "config.json"' in source
        assert '_TOKENIZER_JSON = "tokenizer.json"' in source
        assert '"vocab.txt"' in source
        assert "_VOCAB_TXT" not in source
        assert f"{pm.PINS_MARKER}\n    {{" in source
        assert f'_CONFIG_JSON: "{HASH_1}",' in source
        assert source.endswith(")\n")

    def test_header_docstring_and_pin_definition_are_preserved(self, pm):
        source = pm._render_manifest(_models())
        assert source.startswith('"""Pinned revisions and file hashes')
        assert "class ModelPin" in source
        assert "def _pin(" in source

    def test_rendering_is_deterministic(self, pm):
        models = _models()
        first = pm._render_manifest(models)
        assert pm._render_manifest(json.loads(json.dumps(models))) == first

    def test_rendered_module_round_trips_the_input_pins(self, pm, tmp_path):
        models = _models()
        module = _exec_source(pm._render_manifest(models), tmp_path)
        assert list(module.MODEL_PINS) == [m["repo_id"] for m in models]
        for m in models:
            pin = module.MODEL_PINS[m["repo_id"]]
            assert pin.repo_id == m["repo_id"]
            assert pin.revision == m["main"]["commit"]
            assert pin.kind == m["kind"]
            assert dict(pin.files) == {f["path"]: f["sha256"] for f in m["main"]["files"]}

    def test_malformed_commit_is_rejected(self, pm):
        models = [_model("org/x", "hf-transformers", "main", {"config.json": HASH_1})]
        with pytest.raises(RuntimeError, match="unexpected commit id"):
            pm._render_manifest(models)

    @pytest.mark.parametrize("path", ["pytorch_model.bin", "weights.pt", "x.ckpt", "m.pkl"])
    def test_pickle_weights_are_refused(self, pm, path):
        models = [_model("org/x", "hf-transformers", COMMIT_A, {path: HASH_1})]
        with pytest.raises(RuntimeError, match="refusing to pin pickle"):
            pm._render_manifest(models)

    def test_manifest_without_pins_marker_cannot_be_regenerated(self, pm, sandbox):
        sandbox.write_text("X = 1\n", encoding="utf-8")
        models = _models()
        with pytest.raises(RuntimeError, match="no .* line to regenerate"):
            pm._render_manifest(models)


class TestFromManifestRegeneration:
    def test_models_from_manifest_mirror_committed_pins(self, pm):
        pins = pm._load_pins()
        models = pm._models_from_manifest()
        assert [m["repo_id"] for m in models] == list(pins)
        for m in models:
            pin = pins[m["repo_id"]]
            assert m["kind"] == pin.kind
            assert m["main"]["commit"] == pin.revision
            assert {f["path"]: f["sha256"] for f in m["main"]["files"]} == dict(pin.files)

    def test_regeneration_reproduces_committed_manifest_byte_for_byte(self, pm):
        committed = pm.MANIFEST.read_bytes()
        rendered = pm._render_manifest(pm._models_from_manifest())
        assert rendered.encode("utf-8") == committed

    def test_cli_from_manifest_writes_identical_file(self, pm, sandbox, monkeypatch, capsys):
        committed = sandbox.read_bytes()
        sandbox.write_text(
            sandbox.read_text(encoding="utf-8").replace(pm.PINS_MARKER, "\n\n" + pm.PINS_MARKER),
            encoding="utf-8",
        )
        assert sandbox.read_bytes() != committed
        monkeypatch.setattr(sys, "argv", ["pin_models.py", "--from-manifest", "--write-manifest"])
        assert pm.main() == 0
        assert sandbox.read_bytes() == committed
        assert "wrote packages/core/septum_core/model_pins.py" in capsys.readouterr().err

    def test_missing_manifest_cannot_seed_regeneration(self, pm, sandbox):
        sandbox.unlink()
        assert pm._load_pins() is None
        with pytest.raises(RuntimeError, match="is missing"):
            pm._models_from_manifest()

    def test_cli_writes_json_dump(self, pm, sandbox, tmp_path, monkeypatch):
        out = tmp_path / "survey.json"
        monkeypatch.setattr(sys, "argv", ["pin_models.py", "--from-manifest", "--json", str(out)])
        assert pm.main() == 0
        assert json.loads(out.read_text(encoding="utf-8"))["models"]

    def test_cli_from_json_writes_manifest_and_prints_summary(
        self, pm, sandbox, tmp_path, monkeypatch, capsys
    ):
        survey = {
            "models": [
                {
                    "repo_id": "org/one",
                    "kind": "hf-transformers",
                    "role": "ner-default",
                    "main": {
                        "commit": COMMIT_A,
                        "files": [{"path": "config.json", "sha256": HASH_1}],
                        "weight_formats": ["safetensors"],
                        "file_count": 1,
                        "total_bytes": 2048,
                    },
                }
            ],
            "spacy_wheel": {"filename": "model.whl", "sha256": HASH_2, "size": 5},
        }
        src = tmp_path / "saved.json"
        src.write_text(json.dumps(survey), encoding="utf-8")
        monkeypatch.setattr(
            sys, "argv", ["pin_models.py", "--from-json", str(src), "--write-manifest"]
        )
        assert pm.main() == 0
        out = capsys.readouterr()
        assert "org/one" in out.out
        assert "aaaaaaaaaaaa" in out.out
        assert "2.0 KB" in out.out
        assert f"spaCy wheel model.whl: sha256 {HASH_2}" in out.out
        assert '"org/one": _pin(' in sandbox.read_text(encoding="utf-8")

    def test_from_json_and_from_manifest_are_mutually_exclusive(self, pm, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["pin_models.py", "--from-json", "x", "--from-manifest"])
        with pytest.raises(SystemExit) as exc:
            pm.main()
        assert exc.value.code == 2


class TestDriftHelpers:
    @staticmethod
    def _pin(pm, files=None, revision=COMMIT_A, kind="hf-transformers"):
        return SimpleNamespace(
            revision=revision,
            kind=kind,
            files=files if files is not None else {"config.json": HASH_1, "model.safetensors": HASH_2},
        )

    def test_matching_hashes_yield_no_findings(self, pm):
        pin = self._pin(pm)
        assert pm._file_drift("t", pin, dict(pin.files)) == []

    def test_changed_hash_extra_and_missing_files_are_reported(self, pm):
        pin = self._pin(pm)
        live = {"config.json": HASH_3, "tokenizer.json": HASH_1}
        assert pm._file_drift("t", pin, live) == [
            "t: config.json hash mismatch",
            "t: model.safetensors is pinned but absent upstream",
            "t: tokenizer.json is needed but not pinned",
        ]

    def _api_for(self, monkeypatch, pm, commit, files):
        monkeypatch.setattr(
            pm,
            "_snapshot",
            lambda api, repo, rev, kind, workdir: {
                "commit": commit,
                "files": [{"path": p, "sha256": h} for p, h in files.items()],
            },
        )

    def test_pin_drift_clean(self, pm, monkeypatch, tmp_path):
        pin = self._pin(pm)
        self._api_for(monkeypatch, pm, pin.revision, pin.files)
        assert pm._pin_drift(None, "org/one", pin, "hf-transformers", tmp_path) == []

    def test_pin_drift_unknown_expected_kind_is_not_compared(self, pm, monkeypatch, tmp_path):
        pin = self._pin(pm)
        self._api_for(monkeypatch, pm, pin.revision, pin.files)
        assert pm._pin_drift(None, "org/one", pin, None, tmp_path) == []

    def test_pin_drift_reports_revision_kind_and_hash_changes(self, pm, monkeypatch, tmp_path):
        pin = self._pin(pm)
        self._api_for(monkeypatch, pm, COMMIT_B, {**pin.files, "config.json": HASH_3})
        findings = pm._pin_drift(None, "org/one", pin, "paddle", tmp_path)
        tag = "org/one@" + COMMIT_A[:12]
        assert findings == [
            f"{tag}: kind 'hf-transformers', loader expects 'paddle'",
            f"{tag}: revision resolves to {COMMIT_B[:12]}",
            f"{tag}: config.json hash mismatch",
        ]


class TestCheckCommand:
    def _wire(self, pm, monkeypatch, pins, targets, snapshots):
        monkeypatch.setattr(pm, "HfApi", lambda: object())
        monkeypatch.setattr(pm, "_load_pins", lambda: pins)
        monkeypatch.setattr(pm, "_targets", lambda: targets)
        monkeypatch.setattr(pm, "_snapshot", lambda api, repo, rev, kind, wd: snapshots[repo])

    @staticmethod
    def _pins():
        return {
            "org/one": SimpleNamespace(
                revision=COMMIT_A, kind="hf-transformers", files={"config.json": HASH_1}
            )
        }

    @staticmethod
    def _snap(commit=COMMIT_A, files=None):
        files = files if files is not None else {"config.json": HASH_1}
        return {"commit": commit, "files": [{"path": p, "sha256": h} for p, h in files.items()]}

    def test_exit_zero_when_hub_matches(self, pm, monkeypatch, capsys):
        self._wire(
            pm, monkeypatch, self._pins(),
            [("org/one", "hf-transformers", "ner-default")], {"org/one": self._snap()},
        )
        monkeypatch.setattr(sys, "argv", ["pin_models.py", "--check"])
        assert pm.main() == 0
        assert "1 pin(s), 1 file(s), 0 drift finding(s)" in capsys.readouterr().out

    def test_exit_one_on_hash_drift(self, pm, monkeypatch, capsys):
        self._wire(
            pm, monkeypatch, self._pins(),
            [("org/one", "hf-transformers", "ner-default")],
            {"org/one": self._snap(files={"config.json": HASH_2})},
        )
        assert pm.check() == 1
        out = capsys.readouterr().out
        assert "DRIFT org/one@aaaaaaaaaaaa: config.json hash mismatch" in out

    def test_default_model_without_pin_is_drift(self, pm, monkeypatch, capsys):
        self._wire(
            pm, monkeypatch, self._pins(),
            [
                ("org/one", "hf-transformers", "ner-default"),
                ("org/new", "hf-transformers", "ner-default"),
            ],
            {"org/one": self._snap()},
        )
        assert pm.check() == 1
        assert "DRIFT org/new: default model has no pin" in capsys.readouterr().out

    def test_missing_manifest_exits_one(self, pm, sandbox, capsys):
        sandbox.unlink()
        assert pm.check() == 1
        assert "model_pins.py is missing" in capsys.readouterr().out


class TestNerModelDiscovery:
    SOURCE = textwrap.dedent(
        """
        class Registry:
            DEFAULT_MODEL_MAP = {"xx": ["m/a", "m/b"], "yy": ["m/a"]}
            SUGGESTED_MODELS: list = [
                {"model_id": "m/a", "label": "A"},
                {"model_id": "m/c", "label": "C"},
                {"label": "no id"},
                {"model_id": dynamic},
            ]
        """
    )

    def test_unique_preserves_order_and_excludes(self, pm):
        assert pm._unique(["b", "a", "b", "c"]) == ["b", "a", "c"]
        assert pm._unique(["b", "a", "c"], exclude=["a"]) == ["b", "c"]

    def test_list_constants_collects_literals_in_source_order(self, pm):
        import ast

        tree = ast.parse("x = {'k': ['a', 'b'], 'j': ['c', other]}")
        assert pm._list_constants(tree) == ["a", "b", "c"]

    def test_model_id_values_skips_non_constant_and_other_keys(self, pm):
        import ast

        tree = ast.parse(self.SOURCE)
        assert pm._model_id_values(tree) == ["m/a", "m/c"]

    def test_class_field_value_handles_annotated_plain_and_missing(self, pm):
        import ast

        tree = ast.parse("A: int = 1\nB = 'x'\n")
        assert pm._class_field_value(tree, "A").value == 1
        assert pm._class_field_value(tree, "B").value == "x"
        with pytest.raises(LookupError):
            pm._class_field_value(tree, "C")

    def test_ner_models_split_defaults_from_presets(self, pm, tmp_path, monkeypatch):
        registry = tmp_path / "registry.py"
        registry.write_text(self.SOURCE, encoding="utf-8")
        monkeypatch.setattr(pm, "NER_REGISTRY", registry)
        defaults, presets = pm._ner_models()
        assert defaults == ["m/a", "m/b"]
        assert presets == ["m/c"]

    def test_ner_models_without_defaults_is_an_error(self, pm, tmp_path, monkeypatch):
        registry = tmp_path / "registry.py"
        registry.write_text("DEFAULT_MODEL_MAP = {}\nSUGGESTED_MODELS = []\n", encoding="utf-8")
        monkeypatch.setattr(pm, "NER_REGISTRY", registry)
        with pytest.raises(LookupError, match="No default NER models"):
            pm._ner_models()

    def test_real_registry_yields_defaults_and_no_overlap(self, pm):
        defaults, presets = pm._ner_models()
        assert defaults
        assert not set(defaults) & set(presets)

    def test_string_constant_requires_a_literal(self, pm, tmp_path):
        src = tmp_path / "c.py"
        src.write_text("OK = 'v'\nBAD = compute()\n", encoding="utf-8")
        assert pm._string_constant(src, "OK") == "v"
        with pytest.raises(LookupError, match="not a string literal"):
            pm._string_constant(src, "BAD")


class TestSnapshotAndFilters:
    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("model.safetensors", "safetensors"),
            ("pytorch_model.bin", "pytorch_bin"),
            ("model.safetensors.index.json", None),
            ("README.md", None),
            ("inference.pdiparams", "paddle"),
        ],
    )
    def test_weight_format(self, pm, path, expected):
        assert pm._weight_format(path) == expected

    def test_is_needed_prefers_safetensors_over_pickle(self, pm):
        assert pm._is_needed("model.safetensors", "hf-transformers", set(), True)
        assert not pm._is_needed("pytorch_model.bin", "hf-transformers", set(), True)
        assert pm._is_needed("pytorch_model.bin", "hf-transformers", set(), False)
        assert pm._is_needed("config.json", "hf-transformers", set(), True)
        assert not pm._is_needed("flax_model.msgpack", "hf-transformers", set(), True)

    def test_is_needed_sentence_transformer_modules_and_paddle(self, pm):
        assert pm._is_needed("modules.json", "sentence-transformers", set(), True)
        assert not pm._is_needed("modules.json", "hf-transformers", set(), True)
        assert pm._is_needed("1_Pooling/config.json", "sentence-transformers", {"1_Pooling"}, True)
        assert not pm._is_needed("other/config.json", "sentence-transformers", {"1_Pooling"}, True)
        assert pm._is_needed("inference.pdiparams", "paddle", set(), False)
        assert not pm._is_needed("README.md", "paddle", set(), False)
        assert pm._is_needed("sentencepiece.bpe.model", "hf-transformers", set(), True)

    def test_snapshot_builds_file_table_from_fake_hub(self, pm, tmp_path):
        siblings = [
            SimpleNamespace(rfilename="model.safetensors", size=10, lfs=SimpleNamespace(sha256=HASH_1, size=10)),
            SimpleNamespace(rfilename="pytorch_model.bin", size=9, lfs=SimpleNamespace(sha256=HASH_2, size=9)),
            SimpleNamespace(rfilename="config.json", size=3, lfs=None),
            SimpleNamespace(rfilename="README.md", size=1, lfs=None),
        ]
        downloaded = tmp_path / "config.json"
        downloaded.write_bytes(b"abc")

        class FakeApi:
            def model_info(self, repo_id, revision=None, files_metadata=False):
                return SimpleNamespace(
                    sha=COMMIT_A,
                    siblings=siblings,
                    last_modified=datetime(2026, 1, 1, tzinfo=timezone.utc),
                )

            def hf_hub_download(self, repo_id, filename, revision, local_dir):
                assert filename == "config.json"
                assert revision == COMMIT_A
                return str(downloaded)

        snap = pm._snapshot(FakeApi(), "org/one", None, "hf-transformers", tmp_path)
        assert snap["commit"] == COMMIT_A
        assert snap["weight_formats"] == ["pytorch_bin", "safetensors"]
        assert [f["path"] for f in snap["files"]] == ["config.json", "model.safetensors"]
        by_path = {f["path"]: f for f in snap["files"]}
        assert by_path["model.safetensors"]["hash_source"] == "lfs"
        assert by_path["config.json"]["hash_source"] == "downloaded"
        import hashlib

        assert by_path["config.json"]["sha256"] == hashlib.sha256(b"abc").hexdigest()
        assert snap["total_bytes"] == 13
        assert snap["last_modified"].startswith("2026-01-01")

    def test_snapshot_rejects_unexpected_commit_id(self, pm, tmp_path):
        class FakeApi:
            def model_info(self, repo_id, revision=None, files_metadata=False):
                return SimpleNamespace(sha="not-a-commit", siblings=[], last_modified=None)

        api = FakeApi()
        with pytest.raises(RuntimeError, match="unexpected commit id"):
            pm._snapshot(api, "org/one", None, "hf-transformers", tmp_path)

    def test_human_size_units(self, pm):
        assert pm._human_size(512) == "512.0 B"
        assert pm._human_size(2048) == "2.0 KB"
        assert pm._human_size(5 * 1024**3) == "5.0 GB"
        assert pm._human_size(3 * 1024**4) == "3.0 TB"

    def test_sha256_file_matches_hashlib(self, pm, tmp_path):
        import hashlib

        f = tmp_path / "blob"
        f.write_bytes(b"x" * (3 * (1 << 20) + 7))
        assert pm._sha256_file(f) == hashlib.sha256(f.read_bytes()).hexdigest()

    def test_spacy_wheel_url_read_from_requirements(self, pm, tmp_path, monkeypatch):
        req = tmp_path / "requirements.txt"
        req.write_text(
            "foo==1\nmodel @ https://example.invalid/spacy-models/m.whl \n", encoding="utf-8"
        )
        monkeypatch.setattr(pm, "REQUIREMENTS", req)
        assert pm._spacy_wheel_url() == "https://example.invalid/spacy-models/m.whl"
        req.write_text("foo==1\n", encoding="utf-8")
        with pytest.raises(LookupError):
            pm._spacy_wheel_url()
