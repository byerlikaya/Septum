"""Survey the model weights Septum downloads at runtime and report pin data.

Developer tool, not shipped in any package. It needs network access to the
Hugging Face Hub and to the spaCy model wheel host. For every default model
it records the commit the repository's ``main`` branch points to, every file
needed to load it with its sha256, and the weight formats on offer. The
output is the input for the pinned-model manifest.

Model IDs are read from the source that loads them (by AST, so nothing heavy
is imported): the NER registry defaults and dashboard presets, the embedding
model default, the OCR detection/recognition names, and the auxiliary OCR
sub-models from the installed PaddleX ``OCR`` pipeline config.

Run from the repository root:

    python packages/api/scripts/pin_models.py                 # summary
    python packages/api/scripts/pin_models.py --json out.json # + JSON
    python packages/api/scripts/pin_models.py --write-manifest
    python packages/api/scripts/pin_models.py --from-json out.json --write-manifest
    python packages/api/scripts/pin_models.py --from-manifest --write-manifest
    python packages/api/scripts/pin_models.py --check         # drift check

``--write-manifest`` regenerates ``septum_core/model_pins.py`` from the
``main`` commit of every default model (from a fresh survey, from a saved one
with ``--from-json``, or from the pins already in the manifest with
``--from-manifest``, which needs no network); everything above the generated
file-name constants and ``MODEL_PINS`` in that file (the ``ModelPin``
definition) is kept as is. Review the diff before committing a bump.

``--check`` skips the survey: it re-reads every pin's revision from the Hub
and exits 1 when a pinned commit, file list or hash differs, or when a
default model has no pin. The manifest is loaded by file path, so the check
does not import the rest of septum-core.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import importlib.util
import json
import re
import sys
import tempfile
from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import yaml
from huggingface_hub import HfApi

REPO_ROOT = Path(__file__).resolve().parents[3]
NER_REGISTRY = REPO_ROOT / "packages/core/septum_core/ner_model_registry.py"
VECTOR_STORE = REPO_ROOT / "packages/api/septum_api/services/vector_store.py"
PADDLE_PROVIDER = (
    REPO_ROOT / "packages/api/septum_api/services/ingestion/ocr/paddle_provider.py"
)
REQUIREMENTS = REPO_ROOT / "packages/api/requirements.txt"
MANIFEST = REPO_ROOT / "packages/core/septum_core/model_pins.py"

PADDLE_HF_ORG = "PaddlePaddle"
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")

TOKENIZER_FILES = {
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "added_tokens.json",
    "vocab.txt",
    "vocab.json",
    "merges.txt",
}
SENTENCE_TRANSFORMERS_FILES = {
    "modules.json",
    "config_sentence_transformers.json",
    "sentence_bert_config.json",
}
PADDLE_SKIPPED_FILES = {".gitattributes", "README.md"}
PICKLE_SUFFIXES = {".bin", ".pt", ".pth", ".ckpt", ".pkl", ".pickle"}
WEIGHT_FORMATS = {
    ".safetensors": "safetensors",
    ".bin": "pytorch_bin",
    ".h5": "tensorflow",
    ".msgpack": "flax",
    ".onnx": "onnx",
    ".ot": "rust",
    ".pdiparams": "paddle",
}


def _module_ast(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _class_field_value(tree: ast.Module, name: str) -> ast.AST:
    """Return the value node assigned to a (possibly annotated) class field."""
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == name:
            return node.value
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == name for t in node.targets
        ):
            return node.value
    raise LookupError(f"{name} not found")


def _string_constant(path: Path, name: str) -> str:
    value = _class_field_value(_module_ast(path), name)
    if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
        raise LookupError(f"{name} in {path} is not a string literal")
    return value.value


def _list_constants(node: ast.AST) -> list[str]:
    """Return the constants of every list literal under ``node``, in source order."""
    return [
        elt.value
        for sub in ast.walk(node)
        if isinstance(sub, ast.List)
        for elt in sub.elts
        if isinstance(elt, ast.Constant)
    ]


def _model_id_values(node: ast.AST) -> list[str]:
    """Return the ``model_id`` value of every dict literal under ``node``, in source order."""
    return [
        val.value
        for sub in ast.walk(node)
        if isinstance(sub, ast.Dict)
        for key, val in zip(sub.keys, sub.values)
        if isinstance(key, ast.Constant)
        and key.value == "model_id"
        and isinstance(val, ast.Constant)
    ]


def _unique(values: Iterable[str], exclude: Sequence[str] = ()) -> list[str]:
    unique: list[str] = []
    for value in values:
        if value not in exclude and value not in unique:
            unique.append(value)
    return unique


def _ner_models() -> tuple[list[str], list[str]]:
    """Return (default ensemble models, dashboard preset models) from the registry."""
    tree = _module_ast(NER_REGISTRY)
    defaults = _unique(_list_constants(_class_field_value(tree, "DEFAULT_MODEL_MAP")))
    presets = _unique(
        _model_id_values(_class_field_value(tree, "SUGGESTED_MODELS")),
        exclude=defaults,
    )
    if not defaults:
        raise LookupError(f"No default NER models found in {NER_REGISTRY}")
    return defaults, presets


def _paddle_models() -> list[str]:
    """Return every PaddleOCR sub-model the current constructor arguments load.

    Detection and recognition come from the provider; orientation and
    unwarping sub-models come from the PaddleX ``OCR`` pipeline defaults,
    which the provider does not override.
    """
    names = [
        _string_constant(PADDLE_PROVIDER, "_DET_MODEL_NAME"),
        _string_constant(PADDLE_PROVIDER, "_REC_MODEL_NAME"),
    ]
    spec = importlib.util.find_spec("paddlex")
    if spec is None or not spec.submodule_search_locations:
        raise RuntimeError("paddlex is not installed; cannot resolve OCR sub-models")
    config_path = (
        Path(next(iter(spec.submodule_search_locations))) / "configs/pipelines/OCR.yaml"
    )
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    preprocessor = config.get("SubPipelines", {}).get("DocPreprocessor", {})
    if config.get("use_doc_preprocessor"):
        sub = preprocessor.get("SubModules", {})
        if preprocessor.get("use_doc_orientation_classify"):
            names.append(sub["DocOrientationClassify"]["model_name"])
        if preprocessor.get("use_doc_unwarping"):
            names.append(sub["DocUnwarping"]["model_name"])
    if config.get("use_textline_orientation"):
        names.append(config["SubModules"]["TextLineOrientation"]["model_name"].strip())
    return names


def _spacy_wheel_url() -> str:
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        if " @ " in line and "spacy-models" in line:
            return line.split(" @ ", 1)[1].strip()
    raise LookupError(f"No spaCy model wheel URL in {REQUIREMENTS}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _weight_format(path: str) -> str | None:
    if path.endswith(".index.json"):
        return None
    return WEIGHT_FORMATS.get(Path(path).suffix)


def _st_module_dirs(api: HfApi, repo_id: str, revision: str, workdir: Path) -> set[str]:
    """Return the sub-directories listed in a sentence-transformers modules.json."""
    path = api.hf_hub_download(
        repo_id, "modules.json", revision=revision, local_dir=workdir / "modules"
    )
    modules = json.loads(Path(path).read_text(encoding="utf-8"))
    return {m["path"] for m in modules if m.get("path")}


def _is_needed(
    path: str, kind: str, module_dirs: set[str], has_safetensors: bool
) -> bool:
    """Decide whether the loader for ``kind`` reads ``path``."""
    if kind == "paddle":
        return path not in PADDLE_SKIPPED_FILES
    name = Path(path).name
    parent = str(Path(path).parent)
    if parent != ".":
        return parent in module_dirs and name.endswith(".json")
    fmt = _weight_format(path)
    if fmt == "safetensors" or name == "model.safetensors.index.json":
        return True
    if fmt == "pytorch_bin" or name == "pytorch_model.bin.index.json":
        return not has_safetensors
    if name == "config.json" or name in TOKENIZER_FILES:
        return True
    if kind == "sentence-transformers" and name in SENTENCE_TRANSFORMERS_FILES:
        return True
    return name.endswith(".model") and fmt is None


def _snapshot(
    api: HfApi, repo_id: str, revision: str | None, kind: str, workdir: Path
) -> dict[str, Any]:
    """Describe one repo commit: SHA, formats and the hashed files needed to load it."""
    info = api.model_info(repo_id, revision=revision, files_metadata=True)
    commit = info.sha
    if not commit or not COMMIT_RE.match(commit):
        raise RuntimeError(f"{repo_id}@{revision}: unexpected commit id {commit!r}")
    siblings = info.siblings or []
    formats = sorted({f for s in siblings if (f := _weight_format(s.rfilename))})
    has_safetensors = "safetensors" in formats
    module_dirs: set[str] = set()
    if kind == "sentence-transformers":
        module_dirs = _st_module_dirs(api, repo_id, commit, workdir)
    files = []
    for sib in sorted(siblings, key=lambda s: s.rfilename):
        if not _is_needed(sib.rfilename, kind, module_dirs, has_safetensors):
            continue
        if sib.lfs is not None:
            sha256, size, source = sib.lfs.sha256, sib.lfs.size, "lfs"
        else:
            local = api.hf_hub_download(
                repo_id, sib.rfilename, revision=commit, local_dir=workdir / commit
            )
            sha256, size, source = _sha256_file(Path(local)), sib.size, "downloaded"
        files.append(
            {
                "path": sib.rfilename,
                "size": size,
                "sha256": sha256,
                "hash_source": source,
            }
        )
    return {
        "commit": commit,
        "last_modified": info.last_modified.isoformat() if info.last_modified else None,
        "weight_formats": formats,
        "files": files,
        "file_count": len(files),
        "total_bytes": sum(f["size"] or 0 for f in files),
    }


def _survey_repo(
    api: HfApi, repo_id: str, kind: str, role: str, workdir: Path
) -> dict[str, Any]:
    print(f"  surveying {repo_id} ...", file=sys.stderr)
    main = _snapshot(api, repo_id, None, kind, workdir)
    return {"repo_id": repo_id, "kind": kind, "role": role, "main": main}


def _survey_spacy(url: str, workdir: Path) -> dict[str, Any]:
    print(f"  downloading {url} ...", file=sys.stderr)
    target = workdir / "spacy" / url.rsplit("/", 1)[1]
    target.parent.mkdir(parents=True, exist_ok=True)
    with httpx.stream("GET", url, follow_redirects=True, timeout=120) as resp:
        resp.raise_for_status()
        with target.open("wb") as fh:
            for chunk in resp.iter_bytes():
                fh.write(chunk)
    sha256 = _sha256_file(target)
    return {
        "url": url,
        "filename": target.name,
        "size": target.stat().st_size,
        "sha256": sha256,
        "requirement_line": f"{url} --hash=sha256:{sha256}",
    }


def _library_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for dist in (
        "huggingface_hub",
        "transformers",
        "sentence-transformers",
        "paddleocr",
        "paddlex",
        "openai-whisper",
        "spacy",
    ):
        try:
            versions[dist] = importlib.metadata.version(dist)
        except importlib.metadata.PackageNotFoundError:
            versions[dist] = None
    return versions


def _targets() -> list[tuple[str, str, str]]:
    """Return (repo id, kind, role) for every model Septum pins."""
    ner_defaults, ner_presets = _ner_models()
    targets = [(m, "hf-transformers", "ner-default") for m in ner_defaults]
    targets += [(m, "hf-transformers", "ner-preset") for m in ner_presets]
    targets.append(
        (
            _string_constant(VECTOR_STORE, "_DEFAULT_ST_MODEL"),
            "sentence-transformers",
            "embedding",
        )
    )
    targets += [(f"{PADDLE_HF_ORG}/{n}", "paddle", "ocr") for n in _paddle_models()]
    return targets


def survey() -> dict[str, Any]:
    """Collect pin data for every default model."""
    api = HfApi()
    targets = _targets()
    with tempfile.TemporaryDirectory(prefix="pin_models_") as tmp:
        workdir = Path(tmp)
        models = [
            _survey_repo(api, repo, kind, role, workdir) for repo, kind, role in targets
        ]
        spacy_wheel = _survey_spacy(_spacy_wheel_url(), workdir)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "library_versions": _library_versions(),
        "models": models,
        "spacy_wheel": spacy_wheel,
        "whisper": "Already verified: openai-whisper checks the sha256 embedded in each "
        "checkpoint URL (septum_api.services.whisper_models).",
    }


def _human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def print_summary(data: dict[str, Any]) -> None:
    rows = [("repo", "role", "main commit", "formats", "files", "size")]
    for m in data["models"]:
        main = m["main"]
        rows.append(
            (
                m["repo_id"],
                m["role"],
                main["commit"][:12],
                ",".join(main["weight_formats"]),
                str(main["file_count"]),
                _human_size(main["total_bytes"]),
            )
        )
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    for row in rows:
        print("  ".join(cell.ljust(w) for cell, w in zip(row, widths)))
    wheel = data["spacy_wheel"]
    print(
        f"\nspaCy wheel {wheel['filename']}: sha256 {wheel['sha256']} ({_human_size(wheel['size'])})"
    )


PINS_MARKER = "MODEL_PINS: Mapping[str, ModelPin] = MappingProxyType("
FILE_NAMES_MARKER = "# --- file names repeated across the pins (generated) ---"
REPEATED_FILE_MIN_USES = 3


def _manifest_preamble() -> str:
    """Return the manifest's hand-kept part: everything above the generated file names."""
    head, marker, _ = MANIFEST.read_text(encoding="utf-8").partition(PINS_MARKER)
    if not marker:
        raise RuntimeError(f"{MANIFEST.name}: no {PINS_MARKER!r} line to regenerate")
    return head.partition(FILE_NAMES_MARKER)[0]


def _file_constants(models: list[dict[str, Any]]) -> dict[str, str]:
    """Name every file path that several pins list: path -> module constant."""
    uses = Counter(f["path"] for m in models for f in m["main"]["files"])
    names: dict[str, str] = {}
    for path in sorted(p for p, n in uses.items() if n >= REPEATED_FILE_MIN_USES):
        name = "_" + re.sub(r"\W", "_", path).upper()
        if name in names.values():
            raise RuntimeError(f"{path}: constant name {name} is already taken")
        names[path] = name
    return names


def _render_pin(m: dict[str, Any], names: dict[str, str]) -> list[str]:
    """Render one ``_pin(...)`` entry; file paths that have a constant reference it."""
    main = m["main"]
    if not COMMIT_RE.match(main["commit"]):
        raise RuntimeError(f"{m['repo_id']}: unexpected commit id {main['commit']!r}")
    pickles = [
        f["path"] for f in main["files"] if Path(f["path"]).suffix in PICKLE_SUFFIXES
    ]
    if pickles:
        raise RuntimeError(f"{m['repo_id']}: refusing to pin pickle weights {pickles}")
    lines = [
        f"        {json.dumps(m['repo_id'])}: _pin(",
        f"            {json.dumps(m['repo_id'])},",
        f"            {json.dumps(main['commit'])},",
        f"            {json.dumps(m['kind'])},",
        "            {",
    ]
    for f in main["files"]:
        key = names[f["path"]] if f["path"] in names else json.dumps(f["path"])
        lines.append(f"                {key}: {json.dumps(f['sha256'])},")
    return lines + ["            },", "        ),"]


def _render_manifest(models: list[dict[str, Any]]) -> str:
    """Render ``model_pins.py`` source from surveyed ``main`` snapshots."""
    names = _file_constants(models)
    lines = [_manifest_preamble().rstrip("\n"), "", "", FILE_NAMES_MARKER]
    lines += [f"{name} = {json.dumps(path)}" for path, name in names.items()]
    lines += ["", "", PINS_MARKER, "    {"]
    for m in models:
        lines += _render_pin(m, names)
    lines += ["    }", ")", ""]
    return "\n".join(lines)


def _load_pins() -> dict[str, Any] | None:
    """Load ``MODEL_PINS`` from the manifest file without importing septum-core."""
    if not MANIFEST.exists():
        return None
    spec = importlib.util.spec_from_file_location("_septum_model_pins", MANIFEST)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return dict(module.MODEL_PINS)


def _models_from_manifest() -> list[dict[str, Any]]:
    """Rebuild the ``main`` snapshots from the pins already in the manifest (no network)."""
    pins = _load_pins()
    if pins is None:
        raise RuntimeError(f"{MANIFEST.name} is missing; nothing to regenerate from")
    return [
        {
            "repo_id": repo_id,
            "kind": pin.kind,
            "main": {
                "commit": pin.revision,
                "files": [
                    {"path": path, "sha256": sha256}
                    for path, sha256 in sorted(pin.files.items())
                ],
            },
        }
        for repo_id, pin in pins.items()
    ]


def _file_drift(tag: str, pin: Any, live: dict[str, str]) -> list[str]:
    """Compare the hashes the Hub serves for a pin's files against the pinned ones."""
    drift: list[str] = []
    for path in sorted(set(live) | set(pin.files)):
        if path not in pin.files:
            drift.append(f"{tag}: {path} is needed but not pinned")
        elif path not in live:
            drift.append(f"{tag}: {path} is pinned but absent upstream")
        elif live[path] != pin.files[path]:
            drift.append(f"{tag}: {path} hash mismatch")
    return drift


def _pin_drift(
    api: HfApi, repo_id: str, pin: Any, expected_kind: str | None, workdir: Path
) -> list[str]:
    """Re-read one pin's revision from the Hub and list everything that drifted."""
    tag = f"{repo_id}@{pin.revision[:12]}"
    drift: list[str] = []
    if expected_kind is not None and expected_kind != pin.kind:
        drift.append(f"{tag}: kind {pin.kind!r}, loader expects {expected_kind!r}")
    print(f"  checking {tag} ...", file=sys.stderr)
    snap = _snapshot(api, repo_id, pin.revision, pin.kind, workdir)
    if snap["commit"] != pin.revision:
        drift.append(f"{tag}: revision resolves to {snap['commit'][:12]}")
    live = {f["path"]: f["sha256"] for f in snap["files"]}
    return drift + _file_drift(tag, pin, live)


def check() -> int:
    """Compare the pinned manifest against the Hub; return a process exit code."""
    pins = _load_pins()
    if pins is None:
        print(f"--check: {MANIFEST.relative_to(REPO_ROOT)} is missing.")
        return 1
    api = HfApi()
    kinds = {repo: kind for repo, kind, _ in _targets()}
    drift = [
        f"{repo}: default model has no pin" for repo in sorted(set(kinds) - set(pins))
    ]
    with tempfile.TemporaryDirectory(prefix="pin_models_check_") as tmp:
        for repo_id, pin in pins.items():
            drift += _pin_drift(api, repo_id, pin, kinds.get(repo_id), Path(tmp))
    for line in drift:
        print(f"DRIFT {line}")
    print(
        f"--check: {len(pins)} pin(s), {sum(len(p.files) for p in pins.values())} file(s), {len(drift)} drift finding(s)."
    )
    return 1 if drift else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--json", type=Path, help="write the machine-readable survey here"
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--from-json", type=Path, help="read a saved survey instead of surveying"
    )
    source.add_argument(
        "--from-manifest",
        action="store_true",
        help="rebuild from the pins already in septum_core/model_pins.py (no network)",
    )
    parser.add_argument(
        "--write-manifest",
        action="store_true",
        help="regenerate septum_core/model_pins.py from the survey's main commits",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare septum_core/model_pins.py against the Hub (no survey)",
    )
    args = parser.parse_args()
    if args.check:
        return check()
    if args.from_manifest:
        data = {"models": _models_from_manifest()}
    elif args.from_json:
        data = json.loads(args.from_json.read_text(encoding="utf-8"))
    else:
        data = survey()
    if args.json:
        args.json.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {args.json}", file=sys.stderr)
    if args.write_manifest:
        MANIFEST.write_text(_render_manifest(data["models"]), encoding="utf-8")
        print(f"wrote {MANIFEST.relative_to(REPO_ROOT)}", file=sys.stderr)
    if not args.from_manifest:
        print_summary(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
