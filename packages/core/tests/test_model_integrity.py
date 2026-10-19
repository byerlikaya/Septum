"""Pinned model integrity: hash verification, refetch policy, specs and user pins.

No network: ``huggingface_hub.snapshot_download`` is replaced by a fake that
writes tiny files into a temporary cache laid out like the Hub cache.
"""

from __future__ import annotations

import errno
import hashlib
import json
import logging
import operator
import os
import pickle
import re
import stat
import threading
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import huggingface_hub
import pytest
from huggingface_hub import constants
from huggingface_hub.errors import LocalEntryNotFoundError
from septum_core import model_integrity
from septum_core.model_integrity import (
    USER_PINS_FILENAME,
    USER_PINS_PATH_ENV,
    ModelIntegrityError,
    ModelRefusal,
    UnpinnedModelError,
    ensure_model_snapshot,
    ensure_pinned_snapshot,
    ensure_user_pinned_snapshot,
    fetch_pinned_snapshot,
    fetch_user_pinned_snapshot,
    parse_model_spec,
    require_pin,
    split_model_specs,
    user_pins_path,
    verify_files,
)
from septum_core.model_pins import MODEL_PINS, ModelPin

REPO = "acme/tiny-model"
REV = "a" * 40
OTHER_REV = "b" * 40
GOOD = {"config.json": b'{"a": 1}', "model.safetensors": b"weights-v1"}
_PICKLE_SUFFIXES = {".bin", ".pt", ".pth", ".ckpt", ".pkl", ".pickle"}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _pin(files: dict[str, bytes] = GOOD, repo: str = REPO, rev: str = REV) -> ModelPin:
    return ModelPin(
        repo, rev, "hf-transformers", MappingProxyType({k: _sha(v) for k, v in files.items()})
    )


def _write(root: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


class FakeHub:
    """Stand-in for ``snapshot_download``: serves scripted file sets in call order."""

    def __init__(self, *responses: dict[str, bytes] | BaseException) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(
        self, repo_id, *, revision, allow_patterns, cache_dir, local_files_only, force_download
    ):
        self.calls.append(
            {
                "repo_id": repo_id,
                "revision": revision,
                "local_files_only": local_files_only,
                "force_download": force_download,
            }
        )
        response = self.responses[min(len(self.calls), len(self.responses)) - 1]
        if isinstance(response, BaseException):
            raise response
        snapshot = _snapshot(Path(cache_dir), repo_id, revision)
        snapshot.mkdir(parents=True, exist_ok=True)
        return str(_write(snapshot, response))


@pytest.fixture
def online(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(constants, "is_offline_mode", lambda: False)


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(constants, "is_offline_mode", lambda: True)


def _install(monkeypatch: pytest.MonkeyPatch, hub: FakeHub) -> FakeHub:
    monkeypatch.setattr(huggingface_hub, "snapshot_download", hub)
    return hub


# --- verify_files ---------------------------------------------------------


def test_verify_files_accepts_matching_directory(tmp_path: Path) -> None:
    root = _write(tmp_path / "m", GOOD)

    verify_files(root, _pin())


def test_verify_files_refuses_a_flipped_byte(tmp_path: Path) -> None:
    root = _write(tmp_path / "m", {**GOOD, "model.safetensors": b"weights-v2"})

    with pytest.raises(ModelIntegrityError) as exc:
        verify_files(root, _pin())

    assert exc.value.code == "model_integrity_failed"
    assert "model.safetensors" in exc.value.reason
    assert str(tmp_path) not in str(exc.value)


def test_verify_files_refuses_a_missing_pinned_file(tmp_path: Path) -> None:
    root = _write(tmp_path / "m", {"config.json": GOOD["config.json"]})

    with pytest.raises(ModelIntegrityError, match="missing"):
        verify_files(root, _pin())


def test_verify_files_refuses_a_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(ModelIntegrityError, match="directory is missing"):
        verify_files(tmp_path / "absent", _pin())


@pytest.mark.parametrize(
    "stray",
    ["pytorch_model.bin", "sub/dir/weights.PT", "notes.txt", "tokenizer.json"],
    ids=["pickle", "nested-pickle", "any-extra-file", "planted-tokenizer"],
)
def test_verify_files_refuses_any_unpinned_file(tmp_path: Path, stray: str) -> None:
    root = _write(tmp_path / "m", {**GOOD, stray: b"planted"})

    with pytest.raises(ModelIntegrityError) as exc:
        verify_files(root, _pin())

    assert exc.value.reason == f"unpinned file '{stray}' is present"


def test_verify_files_refuses_a_planted_symlink(tmp_path: Path) -> None:
    root = _write(tmp_path / "m", GOOD)
    (tmp_path / "evil.json").write_text("{}")
    os.symlink(tmp_path / "evil.json", root / "tokenizer.json")

    with pytest.raises(ModelIntegrityError, match="unpinned file 'tokenizer.json'"):
        verify_files(root, _pin())


def test_verify_files_refuses_a_dangling_planted_symlink(tmp_path: Path) -> None:
    root = _write(tmp_path / "m", GOOD)
    os.symlink(tmp_path / "nowhere", root / "added_tokens.json")

    with pytest.raises(ModelIntegrityError, match="unpinned file 'added_tokens.json'"):
        verify_files(root, _pin())


def test_verify_files_accepts_the_hub_cache_layout_of_symlinked_blobs(tmp_path: Path) -> None:
    blobs = tmp_path / "blobs"
    root = tmp_path / "snapshots" / REV
    root.mkdir(parents=True)
    for rel, data in GOOD.items():
        _write(blobs, {_sha(data): data})
        os.symlink(blobs / _sha(data), root / rel)

    verify_files(root, _pin())


def test_verify_files_refuses_a_file_that_changes_while_hashed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write(tmp_path / "m", GOOD)
    real = model_integrity._hash_file

    def _moving(path, *args, **kwargs):
        digest, _identity, git = real(path, *args, **kwargs)
        return digest, None, git

    monkeypatch.setattr(model_integrity, "_hash_file", _moving)

    with pytest.raises(ModelIntegrityError, match="changed while it was verified"):
        verify_files(root, _pin())


@pytest.mark.parametrize("rel", ["../escape.json", "a/../../escape.json", "/etc/passwd"])
def test_verify_files_refuses_a_non_relative_pinned_path(tmp_path: Path, rel: str) -> None:
    (tmp_path / "escape.json").write_bytes(b"x")
    root = tmp_path / "m"
    root.mkdir()
    pin = ModelPin(REPO, REV, "hf-transformers", MappingProxyType({rel: _sha(b"x")}))

    with pytest.raises(ModelIntegrityError, match="not repo-relative"):
        verify_files(root, pin)


def test_verified_file_is_not_rehashed_while_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write(tmp_path / "m", GOOD)
    verify_files(root, _pin())
    hashed: list[Path] = []
    real = model_integrity._hash_file
    monkeypatch.setattr(
        model_integrity, "_hash_file", lambda p, *a, **k: hashed.append(p) or real(p, *a, **k)
    )

    verify_files(root, _pin())

    assert hashed == []


def test_stat_cache_is_invalidated_when_the_file_is_replaced(tmp_path: Path) -> None:
    root = _write(tmp_path / "m", GOOD)
    verify_files(root, _pin())
    tampered = tmp_path / "tampered"
    tampered.write_bytes(b"weights-v2")
    os.replace(tampered, root / "model.safetensors")

    with pytest.raises(ModelIntegrityError, match="model.safetensors"):
        verify_files(root, _pin())


def test_stat_cache_is_invalidated_when_the_file_is_rewritten_in_place(tmp_path: Path) -> None:
    root = _write(tmp_path / "m", GOOD)
    verify_files(root, _pin())
    target = root / "model.safetensors"
    st = target.stat()
    target.write_bytes(b"weights-v2")
    os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns))

    with pytest.raises(ModelIntegrityError):
        verify_files(root, _pin())


# --- manifest -------------------------------------------------------------


@pytest.mark.parametrize("repo_id", sorted(MODEL_PINS))
def test_manifest_entry_is_well_formed(repo_id: str) -> None:
    pin = MODEL_PINS[repo_id]

    assert pin.repo_id == repo_id
    assert re.fullmatch(r"[0-9a-f]{40}", pin.revision)
    assert pin.kind in {"hf-transformers", "sentence-transformers", "paddle"}
    assert pin.files
    for rel, digest in pin.files.items():
        assert re.fullmatch(r"[0-9a-f]{64}", digest), rel
        assert not rel.startswith("/") and ".." not in rel.split("/"), rel
        assert Path(rel).suffix.lower() not in _PICKLE_SUFFIXES, rel
    if pin.kind != "paddle":
        assert any(rel.endswith(".safetensors") for rel in pin.files)


def test_manifest_file_tables_are_read_only() -> None:
    pin = next(iter(MODEL_PINS.values()))

    with pytest.raises(TypeError):
        operator.setitem(pin.files, "evil.bin", "0" * 64)


# --- parse_model_spec -----------------------------------------------------


def test_bare_manifest_id_resolves_to_its_pin() -> None:
    repo_id, pin = next(iter(MODEL_PINS.items()))

    assert parse_model_spec(repo_id) is pin


def test_manifest_id_at_its_pinned_revision_resolves_to_the_pin() -> None:
    repo_id, pin = next(iter(MODEL_PINS.items()))

    assert parse_model_spec(f"{repo_id}@{pin.revision}") is pin


def test_manifest_id_at_another_revision_is_user_pinned() -> None:
    repo_id = next(iter(MODEL_PINS))

    assert parse_model_spec(f"{repo_id}@{OTHER_REV}") == (repo_id, OTHER_REV)


def test_unknown_id_with_commit_is_user_pinned() -> None:
    assert parse_model_spec(f"{REPO}@{REV}") == (REPO, REV)


@pytest.mark.parametrize(
    "spec",
    [
        REPO,
        "no-slash",
        f"{REPO}@main",
        f"{REPO}@{'a' * 39}",
        f"{REPO}@{'A' * 40}",
        f"{REPO}@{REV}x",
        "../../etc/passwd",
        "a/b/c",
        "",
        f"a--b/c@{REV}",
        f"org/x.@{REV}",
    ],
)
def test_unpinned_or_malformed_spec_is_refused(spec: str) -> None:
    with pytest.raises(UnpinnedModelError) as exc:
        parse_model_spec(spec)

    assert exc.value.code == "ner_model_unpinned"
    assert exc.value.spec == spec
    if spec:
        assert spec not in str(exc.value)


def test_unpinned_spec_never_reaches_the_hub(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = _install(monkeypatch, FakeHub(GOOD))

    with pytest.raises(UnpinnedModelError):
        ensure_model_snapshot(REPO, cache_dir=tmp_path)

    assert hub.calls == []


# --- ensure_pinned_snapshot -----------------------------------------------


def test_good_download_is_fetched_once_at_the_pinned_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, online: None
) -> None:
    hub = _install(monkeypatch, FakeHub(GOOD))

    path = ensure_pinned_snapshot(_pin(), cache_dir=tmp_path)

    assert (path / "model.safetensors").read_bytes() == GOOD["model.safetensors"]
    assert [c["revision"] for c in hub.calls] == [REV]


def test_bad_download_is_purged_and_refetched_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, online: None
) -> None:
    bad = {**GOOD, "model.safetensors": b"tampered", "stale.bin": b"x"}
    hub = FakeHub(bad, GOOD)
    seen_stale: list[bool] = []

    def _second_call_sees_clean_cache(*args, **kwargs):
        snapshot = tmp_path / f"models--{REPO.replace('/', '--')}" / "snapshots" / REV
        seen_stale.append((snapshot / "stale.bin").exists())
        return hub(*args, **kwargs)

    monkeypatch.setattr(huggingface_hub, "snapshot_download", _second_call_sees_clean_cache)

    path = ensure_pinned_snapshot(_pin(), cache_dir=tmp_path)

    assert [c["force_download"] for c in hub.calls] == [False, True]
    assert seen_stale == [False, False]
    assert not (path / "stale.bin").exists()


def test_second_bad_download_raises_after_exactly_two_fetches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, online: None
) -> None:
    hub = _install(monkeypatch, FakeHub({**GOOD, "model.safetensors": b"tampered"}))

    with pytest.raises(ModelIntegrityError) as exc:
        ensure_pinned_snapshot(_pin(), cache_dir=tmp_path)

    assert len(hub.calls) == 2
    assert exc.value.code == "model_integrity_failed"


class SharedStoreHub:
    """Stand-in for ``snapshot_download`` over the Hub's shared (Xet) blob layout.

    Each snapshot file links to ``blobs/<etag>`` in the repo, itself a symlink
    to ``<cache>/blobs/<xx>/<hash>``. Unforced, an existing repo blob is
    re-linked as is; forced, the content is fetched again into the store.
    """

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.calls: list[bool] = []

    def __call__(
        self, repo_id, *, revision, allow_patterns, cache_dir, local_files_only, force_download
    ):
        self.calls.append(force_download)
        snapshot = _snapshot(Path(cache_dir), repo_id, revision)
        snapshot.mkdir(parents=True, exist_ok=True)
        for rel, data in self.files.items():
            _shared_store_file(Path(cache_dir), snapshot, rel, data, refetch=force_download)
        return str(snapshot)


def _shared_store_file(
    cache: Path, snapshot: Path, rel: str, data: bytes, refetch: bool = True
) -> None:
    """Link ``snapshot/rel`` through ``blobs/<etag>`` to a shared-store entry holding ``data``."""
    etag = _sha(rel.encode())
    store = cache / "blobs" / etag[:2] / etag
    blob = snapshot.parent.parent / "blobs" / etag
    if refetch or not os.path.lexists(blob):
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_bytes(data)
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.unlink(missing_ok=True)
        os.symlink(store, blob)
    pointer = snapshot / rel
    pointer.unlink(missing_ok=True)
    os.symlink(blob, pointer)


def test_shared_store_mismatch_is_healed_by_a_forced_refetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, online: None
) -> None:
    snapshot = _snapshot(tmp_path)
    snapshot.mkdir(parents=True)
    for rel, data in {**GOOD, "model.safetensors": b"tampered"}.items():
        _shared_store_file(tmp_path, snapshot, rel, data)
    hub = _install(monkeypatch, SharedStoreHub(GOOD))

    path, fetched = fetch_pinned_snapshot(_pin(), cache_dir=tmp_path)

    assert fetched is True
    assert hub.calls == [True]
    assert (path / "model.safetensors").read_bytes() == GOOD["model.safetensors"]


def test_shared_store_mismatch_is_not_healed_by_an_unforced_refetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, online: None
) -> None:
    snapshot = _snapshot(tmp_path)
    snapshot.mkdir(parents=True)
    for rel, data in {**GOOD, "model.safetensors": b"tampered"}.items():
        _shared_store_file(tmp_path, snapshot, rel, data)
    hub = SharedStoreHub(GOOD)
    monkeypatch.setattr(
        huggingface_hub,
        "snapshot_download",
        lambda *a, **k: hub(*a, **{**k, "force_download": False}),
    )

    with pytest.raises(ModelIntegrityError, match="model.safetensors"):
        fetch_pinned_snapshot(_pin(), cache_dir=tmp_path)


def test_fetch_failure_online_is_a_coded_integrity_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, online: None
) -> None:
    _install(monkeypatch, FakeHub(LocalEntryNotFoundError("nope")))

    with pytest.raises(ModelIntegrityError, match="could not be fetched"):
        ensure_pinned_snapshot(_pin(), cache_dir=tmp_path)


def _snapshot(cache: Path, repo: str = REPO, rev: str = REV) -> Path:
    return cache / f"models--{repo.replace('/', '--')}" / "snapshots" / rev


def test_offline_missing_files_fail_at_once_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: None
) -> None:
    hub = _install(monkeypatch, FakeHub(GOOD))

    with pytest.raises(ModelIntegrityError, match="offline mode"):
        ensure_pinned_snapshot(_pin(), cache_dir=tmp_path)

    assert hub.calls == []


def test_offline_mismatch_is_refused_without_purge_or_refetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: None
) -> None:
    snapshot = _write(_snapshot(tmp_path), {**GOOD, "model.safetensors": b"tampered"})
    hub = _install(monkeypatch, FakeHub(GOOD))

    with pytest.raises(ModelIntegrityError, match="model.safetensors"):
        ensure_pinned_snapshot(_pin(), cache_dir=tmp_path)

    assert hub.calls == []
    assert (snapshot / "model.safetensors").read_bytes() == b"tampered"


# --- fetch_pinned_snapshot ------------------------------------------------


def test_warm_cache_is_returned_without_any_hub_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, online: None
) -> None:
    snapshot = _write(_snapshot(tmp_path), GOOD)
    hub = _install(monkeypatch, FakeHub(AssertionError("no hub call expected")))

    assert fetch_pinned_snapshot(_pin(), cache_dir=tmp_path) == (snapshot, False)
    assert hub.calls == []


def test_cold_cache_is_fetched_once_and_reported_as_fetched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, online: None
) -> None:
    hub = _install(monkeypatch, FakeHub(GOOD))

    path, fetched = fetch_pinned_snapshot(_pin(), cache_dir=tmp_path)

    assert fetched is True
    assert path == _snapshot(tmp_path)
    assert len(hub.calls) == 1


def test_cached_mismatch_is_purged_and_fetched_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, online: None
) -> None:
    _write(_snapshot(tmp_path), {**GOOD, "model.safetensors": b"tampered", "stale.bin": b"x"})
    hub = _install(monkeypatch, FakeHub(GOOD))

    path, fetched = fetch_pinned_snapshot(_pin(), cache_dir=tmp_path)

    assert fetched is True
    assert [c["force_download"] for c in hub.calls] == [True]
    assert (path / "model.safetensors").read_bytes() == GOOD["model.safetensors"]
    assert not (path / "stale.bin").exists()


def test_cached_mismatch_with_a_bad_refetch_raises_after_one_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, online: None
) -> None:
    _write(_snapshot(tmp_path), {**GOOD, "model.safetensors": b"tampered"})
    hub = _install(monkeypatch, FakeHub({**GOOD, "model.safetensors": b"still-bad"}))

    with pytest.raises(ModelIntegrityError):
        fetch_pinned_snapshot(_pin(), cache_dir=tmp_path)

    assert len(hub.calls) == 1


# --- require_pin and refusal types ----------------------------------------


def test_require_pin_returns_the_manifest_pin() -> None:
    repo_id, pin = next(iter(MODEL_PINS.items()))

    assert require_pin(repo_id) is pin


def test_require_pin_refuses_an_unknown_id() -> None:
    with pytest.raises(ModelIntegrityError, match="no pin") as exc:
        require_pin(REPO)

    assert exc.value.model_id == REPO


@pytest.mark.parametrize(
    "exc",
    [ModelIntegrityError(REPO, "file 'x' does not match its pinned hash"), UnpinnedModelError(REPO)],
    ids=["integrity", "unpinned"],
)
def test_refusals_share_a_base_and_survive_pickling(exc: ModelRefusal) -> None:
    clone = pickle.loads(pickle.dumps(exc))

    assert isinstance(exc, ModelRefusal)
    assert type(clone) is type(exc)
    assert clone.code == exc.code
    assert str(clone) == str(exc)
    assert vars(clone) == vars(exc)


# --- _purge -----------------------------------------------------------------


def test_purge_removes_one_revision_and_only_its_blobs(tmp_path: Path) -> None:
    repo = tmp_path / "models--acme--tiny-model"
    blobs = repo / "blobs"
    _write(blobs, {"blob-a": b"a", "blob-b": b"b"})
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"not a cache blob")
    old, kept = repo / "snapshots" / REV, repo / "snapshots" / OTHER_REV
    old.mkdir(parents=True)
    kept.mkdir(parents=True)
    os.symlink(blobs / "blob-a", old / "model.safetensors")
    os.symlink(outside, old / "config.json")
    os.symlink(blobs / "blob-b", kept / "model.safetensors")

    model_integrity._purge(old, tmp_path, REPO)

    assert not old.exists()
    assert not (blobs / "blob-a").exists()
    assert (blobs / "blob-b").read_bytes() == b"b"
    assert (kept / "model.safetensors").read_bytes() == b"b"
    assert outside.read_bytes() == b"not a cache blob"


# --- user pins (trust on first use, checked against Hub metadata) -----------


def _git_sha1(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


class FakeApi:
    """Stand-in for ``HfApi``: ``model_info`` reports digests of ``files`` at the asked revision."""

    def __init__(self, files: dict[str, bytes] = GOOD, sha: str | None = None) -> None:
        self.files = files
        self.sha = sha
        self.calls: list[tuple[str, str]] = []

    def model_info(self, repo_id: str, revision: str, files_metadata: bool):
        assert files_metadata is True
        self.calls.append((repo_id, revision))
        siblings = [
            SimpleNamespace(rfilename=rel, lfs=SimpleNamespace(sha256=_sha(data)), blob_id="0" * 40)
            if rel.endswith(".safetensors")
            else SimpleNamespace(rfilename=rel, lfs=None, blob_id=_git_sha1(data))
            for rel, data in self.files.items()
        ]
        return SimpleNamespace(sha=self.sha or revision, siblings=siblings)


@pytest.fixture
def pins_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "state" / USER_PINS_FILENAME
    monkeypatch.setenv(USER_PINS_PATH_ENV, str(path))
    return path


@pytest.fixture
def cache(tmp_path: Path) -> Path:
    return tmp_path / "cache"


def _install_api(monkeypatch: pytest.MonkeyPatch, api: FakeApi) -> FakeApi:
    monkeypatch.setattr(huggingface_hub, "HfApi", lambda: api)
    return api


def _user_pins(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_user_pins_path_defaults_next_to_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(USER_PINS_PATH_ENV, raising=False)
    monkeypatch.setenv("SEPTUM_CONFIG_PATH", str(tmp_path / "data" / "config.json"))

    assert user_pins_path() == tmp_path / "data" / USER_PINS_FILENAME


def test_first_use_records_hashes_outside_the_model_cache(
    cache: Path, pins_path: Path, monkeypatch: pytest.MonkeyPatch, online: None
) -> None:
    _install(monkeypatch, FakeHub(GOOD))
    _install_api(monkeypatch, FakeApi())

    path = ensure_user_pinned_snapshot(REPO, REV, cache_dir=cache)

    assert path.is_dir()
    assert _user_pins(pins_path) == {
        f"{REPO}@{REV}": {rel: _sha(data) for rel, data in GOOD.items()}
    }
    assert not (cache / USER_PINS_FILENAME).exists()


def test_fetch_user_pinned_snapshot_reports_fetched_then_cached(
    cache: Path, pins_path: Path, monkeypatch: pytest.MonkeyPatch, online: None
) -> None:
    hub = _install(monkeypatch, FakeHub(GOOD))
    _install_api(monkeypatch, FakeApi())

    first = fetch_user_pinned_snapshot(REPO, REV, cache_dir=cache)
    second = fetch_user_pinned_snapshot(REPO, REV, cache_dir=cache)

    assert first == (_snapshot(cache), True)
    assert second == (_snapshot(cache), False)
    assert len(hub.calls) == 1


def test_first_use_downloads_once_and_hashes_each_file_once(
    cache: Path, pins_path: Path, monkeypatch: pytest.MonkeyPatch, online: None
) -> None:
    hub = _install(monkeypatch, FakeHub(GOOD))
    _install_api(monkeypatch, FakeApi())
    hashed: list[str] = []
    real = model_integrity._hash_file
    monkeypatch.setattr(
        model_integrity,
        "_hash_file",
        lambda p, *a, **k: hashed.append(Path(p).name) or real(p, *a, **k),
    )

    ensure_user_pinned_snapshot(REPO, REV, cache_dir=cache)

    assert [c["force_download"] for c in hub.calls] == [True]
    assert sorted(hashed) == sorted(GOOD)


@pytest.mark.parametrize("tampered", ["model.safetensors", "config.json"], ids=["lfs", "git-blob"])
def test_first_use_mismatching_hub_metadata_is_refused_and_not_recorded(
    cache: Path, pins_path: Path, monkeypatch: pytest.MonkeyPatch, online: None, tampered: str
) -> None:
    _install(monkeypatch, FakeHub({**GOOD, tampered: b"served-by-a-mirror"}))
    _install_api(monkeypatch, FakeApi())

    with pytest.raises(ModelIntegrityError, match="does not match the Hub's metadata"):
        ensure_user_pinned_snapshot(REPO, REV, cache_dir=cache)

    assert not pins_path.exists()


def test_first_use_with_metadata_for_another_revision_is_refused(
    cache: Path, pins_path: Path, monkeypatch: pytest.MonkeyPatch, online: None
) -> None:
    _install(monkeypatch, FakeHub(GOOD))
    _install_api(monkeypatch, FakeApi(sha=OTHER_REV))

    with pytest.raises(ModelIntegrityError, match="different revision"):
        ensure_user_pinned_snapshot(REPO, REV, cache_dir=cache)

    assert not pins_path.exists()


def test_first_use_with_an_unexpected_file_is_refused(
    cache: Path, pins_path: Path, monkeypatch: pytest.MonkeyPatch, online: None
) -> None:
    served = {**GOOD, "pytorch_model.bin": b"\x80\x04pickle"}
    _install(monkeypatch, FakeHub(served))
    _install_api(monkeypatch, FakeApi(served))

    with pytest.raises(ModelIntegrityError, match="unpinned file 'pytorch_model.bin'"):
        ensure_user_pinned_snapshot(REPO, REV, cache_dir=cache)

    assert not pins_path.exists()


def test_first_use_offline_is_refused_without_any_hub_access(
    cache: Path, pins_path: Path, monkeypatch: pytest.MonkeyPatch, offline: None
) -> None:
    _write(_snapshot(cache), GOOD)
    hub = _install(monkeypatch, FakeHub(GOOD))
    api = _install_api(monkeypatch, FakeApi())

    with pytest.raises(ModelIntegrityError, match="needs a connection"):
        ensure_user_pinned_snapshot(REPO, REV, cache_dir=cache)

    assert (hub.calls, api.calls) == ([], [])
    assert not pins_path.exists()


def test_first_use_of_a_manifest_pinned_revision_is_refused(
    cache: Path, pins_path: Path, monkeypatch: pytest.MonkeyPatch, online: None
) -> None:
    repo_id, pin = next(iter(MODEL_PINS.items()))
    hub = _install(monkeypatch, FakeHub(GOOD))
    _install_api(monkeypatch, FakeApi())

    with pytest.raises(ModelIntegrityError, match="manifest-pinned"):
        ensure_user_pinned_snapshot(repo_id, pin.revision, cache_dir=cache)

    assert hub.calls == []
    assert not pins_path.exists()


def test_user_pins_file_is_private_and_left_without_temp_files(
    cache: Path, pins_path: Path, monkeypatch: pytest.MonkeyPatch, online: None
) -> None:
    _install(monkeypatch, FakeHub(GOOD))
    _install_api(monkeypatch, FakeApi())

    ensure_user_pinned_snapshot(REPO, REV, cache_dir=cache)

    assert stat.S_IMODE(pins_path.stat().st_mode) == 0o600
    assert [p.name for p in pins_path.parent.iterdir() if p.name.endswith(".tmp")] == []


def test_concurrent_first_uses_of_different_repos_keep_every_record(
    cache: Path, pins_path: Path, monkeypatch: pytest.MonkeyPatch, online: None
) -> None:
    _install(monkeypatch, FakeHub(GOOD))
    _install_api(monkeypatch, FakeApi())
    repos = [f"acme/model-{i}" for i in range(8)]
    barrier = threading.Barrier(len(repos))
    errors: list[BaseException] = []

    def _first_use(repo: str) -> None:
        barrier.wait()
        try:
            ensure_user_pinned_snapshot(repo, REV, cache_dir=cache)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_first_use, args=(r,)) for r in repos]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert sorted(_user_pins(pins_path)) == sorted(f"{r}@{REV}" for r in repos)


def test_later_use_verifies_against_the_recorded_hashes(
    cache: Path, pins_path: Path, monkeypatch: pytest.MonkeyPatch, online: None
) -> None:
    _install(monkeypatch, FakeHub(GOOD))
    _install_api(monkeypatch, FakeApi())
    snapshot = ensure_user_pinned_snapshot(REPO, REV, cache_dir=cache)
    recorded = _user_pins(pins_path)
    swapped = cache / "swapped"
    swapped.write_bytes(b"swapped-on-disk")
    os.replace(swapped, snapshot / "model.safetensors")
    hub = _install(monkeypatch, FakeHub({**GOOD, "model.safetensors": b"swapped-upstream"}))
    api = _install_api(monkeypatch, FakeApi())

    with pytest.raises(ModelIntegrityError, match="model.safetensors"):
        ensure_user_pinned_snapshot(REPO, REV, cache_dir=cache)

    assert len(hub.calls) == 1
    assert api.calls == []
    assert _user_pins(pins_path) == recorded


def test_later_use_with_a_tampered_copy_recovers_from_a_good_refetch(
    cache: Path, pins_path: Path, monkeypatch: pytest.MonkeyPatch, online: None
) -> None:
    _install(monkeypatch, FakeHub(GOOD))
    _install_api(monkeypatch, FakeApi())
    snapshot = ensure_user_pinned_snapshot(REPO, REV, cache_dir=cache)
    (snapshot / "model.safetensors").unlink()
    (snapshot / "model.safetensors").write_bytes(b"swapped-on-disk")
    hub = _install(monkeypatch, FakeHub(GOOD))

    path = ensure_user_pinned_snapshot(REPO, REV, cache_dir=cache)

    assert len(hub.calls) == 1
    assert (path / "model.safetensors").read_bytes() == GOOD["model.safetensors"]


def test_first_use_with_an_incomplete_download_is_refused_and_not_recorded(
    cache: Path, pins_path: Path, monkeypatch: pytest.MonkeyPatch, online: None
) -> None:
    listed = {
        "model-00001-of-00002.safetensors": b"shard-1",
        "model-00002-of-00002.safetensors": b"shard-2",
    }
    _install(monkeypatch, FakeHub({"model-00001-of-00002.safetensors": b"shard-1"}))
    _install_api(monkeypatch, FakeApi(listed))

    with pytest.raises(ModelIntegrityError) as caught:
        ensure_user_pinned_snapshot(REPO, REV, cache_dir=cache)

    assert caught.value.code == "model_integrity_failed"
    assert "'model-00002-of-00002.safetensors' listed by the Hub is missing" in str(caught.value)
    assert str(cache) not in str(caught.value)
    assert not pins_path.exists()


def test_first_use_without_safetensors_is_refused_and_not_recorded(
    cache: Path, pins_path: Path, monkeypatch: pytest.MonkeyPatch, online: None
) -> None:
    served = {"config.json": b"{}"}
    _install(monkeypatch, FakeHub(served))
    _install_api(monkeypatch, FakeApi(served))

    with pytest.raises(ModelIntegrityError, match="safetensors"):
        ensure_user_pinned_snapshot(REPO, REV, cache_dir=cache)

    assert not pins_path.exists()


def test_user_pinned_spec_is_routed_to_the_user_pin_store(
    cache: Path, pins_path: Path, monkeypatch: pytest.MonkeyPatch, online: None
) -> None:
    _install(monkeypatch, FakeHub(GOOD))
    _install_api(monkeypatch, FakeApi())

    ensure_model_snapshot(f"{REPO}@{REV}", cache_dir=cache)

    assert f"{REPO}@{REV}" in _user_pins(pins_path)


@pytest.mark.parametrize(
    ("content", "cause"),
    [("{not json", json.JSONDecodeError), ("[]", None)],
    ids=["malformed-json", "not-an-object"],
)
def test_corrupt_user_pins_file_is_a_coded_refusal_without_the_path(
    cache: Path, pins_path: Path, content: str, cause: type | None
) -> None:
    pins_path.parent.mkdir(parents=True, exist_ok=True)
    pins_path.write_text(content, encoding="utf-8")

    with pytest.raises(ModelIntegrityError) as exc:
        fetch_user_pinned_snapshot(REPO, REV, cache)

    assert exc.value.code == "model_integrity_failed"
    assert str(pins_path) not in str(exc.value)
    if cause is None:
        assert exc.value.__cause__ is None
    else:
        assert isinstance(exc.value.__cause__, cause)


def test_user_pins_path_that_cannot_be_read_is_a_coded_refusal(
    cache: Path, pins_path: Path
) -> None:
    pins_path.mkdir(parents=True)

    with pytest.raises(ModelIntegrityError) as exc:
        fetch_user_pinned_snapshot(REPO, REV, cache)

    assert exc.value.code == "model_integrity_failed"
    assert isinstance(exc.value.__cause__, OSError)
    assert str(pins_path) not in str(exc.value)


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"a": " x , ,y "}, ["x", "y"]),
        ({"a": "x", "b": None, "c": ""}, ["x"]),
        ({}, []),
        (None, []),
    ],
)
def test_split_model_specs_drops_blanks_and_strips(
    overrides: dict[str, str | None] | None, expected: list[str]
) -> None:
    assert split_model_specs(overrides) == expected


# --- offline refusals come before any lock; read-only caches are coded ------

_READ_ONLY = "model cache is read-only"
requires_non_root = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores directory permissions"
)


@pytest.fixture
def no_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        model_integrity, "_file_lock", lambda path: pytest.fail("no lock may be taken")
    )


@pytest.fixture
def read_only():
    """Make directories read-only for one test; permissions are restored in teardown."""
    changed: list[Path] = []

    def _apply(path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o555)
        changed.append(path)
        return path

    yield _apply
    for path in reversed(changed):
        path.chmod(0o755)


def test_offline_missing_snapshot_is_refused_before_any_lock(
    cache: Path, monkeypatch: pytest.MonkeyPatch, offline: None, no_lock: None
) -> None:
    hub = _install(monkeypatch, FakeHub(GOOD))

    with pytest.raises(ModelIntegrityError) as exc:
        fetch_pinned_snapshot(_pin(), cache_dir=cache)

    assert exc.value.reason == model_integrity._OFFLINE_MISSING
    assert hub.calls == []


@requires_non_root
def test_offline_missing_snapshot_in_a_read_only_cache_gets_the_offline_reason(
    cache: Path, read_only, offline: None
) -> None:
    read_only(cache)

    with pytest.raises(ModelIntegrityError) as exc:
        fetch_pinned_snapshot(_pin(), cache_dir=cache)

    assert exc.value.reason == model_integrity._OFFLINE_MISSING


@pytest.mark.parametrize(
    ("served", "reason"),
    [
        ({**GOOD, "model.safetensors": b"tampered"}, "file 'model.safetensors' does not match its pinned hash"),
        ({**GOOD, "tokenizer.json": b"planted"}, "unpinned file 'tokenizer.json' is present"),
    ],
    ids=["mismatch", "planted-file"],
)
def test_offline_failing_snapshot_raises_its_exact_reason_without_a_lock(
    cache: Path, offline: None, no_lock: None, served: dict[str, bytes], reason: str
) -> None:
    _write(_snapshot(cache), served)

    with pytest.raises(ModelIntegrityError) as exc:
        fetch_pinned_snapshot(_pin(), cache_dir=cache)

    assert exc.value.reason == reason


def test_offline_user_pin_first_use_is_refused_before_any_lock(
    cache: Path, pins_path: Path, monkeypatch: pytest.MonkeyPatch, offline: None, no_lock: None
) -> None:
    api = _install_api(monkeypatch, FakeApi())

    with pytest.raises(ModelIntegrityError) as exc:
        fetch_user_pinned_snapshot(REPO, REV, cache_dir=cache)

    assert exc.value.reason == model_integrity._OFFLINE_FIRST_USE
    assert api.calls == []


@requires_non_root
def test_online_read_only_cache_refuses_at_lock_creation(
    cache: Path, monkeypatch: pytest.MonkeyPatch, online: None, read_only
) -> None:
    hub = _install(monkeypatch, FakeHub(GOOD))
    read_only(cache)

    with pytest.raises(ModelIntegrityError) as exc:
        fetch_pinned_snapshot(_pin(), cache_dir=cache)

    assert exc.value.reason == f"{_READ_ONLY} (PermissionError)"
    assert hub.calls == []


@requires_non_root
def test_online_read_only_snapshot_refuses_at_delete(
    cache: Path, monkeypatch: pytest.MonkeyPatch, online: None, read_only
) -> None:
    _write(_snapshot(cache), {**GOOD, "model.safetensors": b"tampered"})
    hub = _install(monkeypatch, FakeHub(GOOD))
    read_only(_snapshot(cache).parent)

    with pytest.raises(ModelIntegrityError) as exc:
        fetch_pinned_snapshot(_pin(), cache_dir=cache)

    assert exc.value.reason == f"{_READ_ONLY} (PermissionError)"
    assert hub.calls == []


@pytest.mark.parametrize(
    "error",
    [PermissionError(13, "Permission denied"), OSError(30, "Read-only file system")],
    ids=["EACCES", "EROFS"],
)
def test_online_read_only_cache_refuses_at_download(
    cache: Path, monkeypatch: pytest.MonkeyPatch, online: None, error: OSError
) -> None:
    _install(monkeypatch, FakeHub(error))

    with pytest.raises(ModelIntegrityError) as exc:
        fetch_pinned_snapshot(_pin(), cache_dir=cache)

    assert exc.value.reason == f"{_READ_ONLY} ({type(error).__name__})"


@requires_non_root
def test_online_read_only_pins_location_refuses_at_record_write(
    cache: Path, pins_path: Path, monkeypatch: pytest.MonkeyPatch, online: None, read_only
) -> None:
    _install(monkeypatch, FakeHub(GOOD))
    _install_api(monkeypatch, FakeApi())
    read_only(pins_path.parent)

    with pytest.raises(ModelIntegrityError) as exc:
        fetch_user_pinned_snapshot(REPO, REV, cache_dir=cache)

    assert exc.value.reason == "user pins file is not writable (PermissionError)"
    assert not pins_path.exists()


def test_erofs_at_record_write_is_coded(
    cache: Path, pins_path: Path, monkeypatch: pytest.MonkeyPatch, online: None
) -> None:
    _install(monkeypatch, FakeHub(GOOD))
    _install_api(monkeypatch, FakeApi())

    def _erofs(*args, **kwargs):
        raise OSError(errno.EROFS, "Read-only file system")

    monkeypatch.setattr(model_integrity.tempfile, "mkstemp", _erofs)

    with pytest.raises(ModelIntegrityError) as exc:
        fetch_user_pinned_snapshot(REPO, REV, cache_dir=cache)

    assert exc.value.reason == "user pins file is not writable (OSError)"


def test_unrelated_os_error_still_propagates(
    cache: Path, monkeypatch: pytest.MonkeyPatch, online: None
) -> None:
    def _full_disk(cache_dir, repo_id):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(model_integrity, "_repo_lock", _full_disk)

    with pytest.raises(OSError) as exc:
        fetch_pinned_snapshot(_pin(), cache_dir=cache)

    assert not isinstance(exc.value, ModelIntegrityError)
    assert exc.value.errno == errno.ENOSPC


def test_enospc_during_download_propagates_unchanged(
    cache: Path, monkeypatch: pytest.MonkeyPatch, online: None
) -> None:
    _install(monkeypatch, FakeHub(OSError(errno.ENOSPC, "No space left on device")))

    with pytest.raises(OSError) as exc:
        fetch_pinned_snapshot(_pin(), cache_dir=cache)

    assert not isinstance(exc.value, ModelIntegrityError)
    assert exc.value.errno == errno.ENOSPC


# --- purge keeps shared blobs and stays inside the cache -----------------------


def test_purge_keeps_a_blob_another_revision_still_links_to(tmp_path: Path) -> None:
    repo = tmp_path / "models--acme--tiny-model"
    blobs = _write(repo / "blobs", {"shared": b"s", "own": b"o"})
    old, kept = repo / "snapshots" / REV, repo / "snapshots" / OTHER_REV
    old.mkdir(parents=True)
    kept.mkdir(parents=True)
    os.symlink(blobs / "shared", old / "config.json")
    os.symlink(blobs / "own", old / "model.safetensors")
    os.symlink(blobs / "shared", kept / "config.json")

    model_integrity._purge(old, tmp_path, REPO)

    assert not old.exists()
    assert not (blobs / "own").exists()
    assert (kept / "config.json").read_bytes() == b"s"


def _cache_with_symlinked(component: str, cache: Path, outside: Path) -> Path:
    """Build a cache whose ``component`` is a symlink to ``outside``; return the snapshot path."""
    repo = cache / "models--acme--tiny-model"
    layout = {
        "repo": repo,
        "snapshots": repo / "snapshots",
        "blobs": repo / "blobs",
        "snapshot": repo / "snapshots" / REV,
    }
    target = layout[component]
    for path in layout.values():
        if path == target:
            break
        path.mkdir(parents=True, exist_ok=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(outside, target)
    snapshot = layout["snapshot"]
    snapshot.mkdir(parents=True, exist_ok=True)
    (repo / "blobs").mkdir(exist_ok=True)
    return snapshot


@pytest.mark.parametrize("component", ["repo", "snapshots", "blobs", "snapshot"])
def test_purge_refuses_a_symlinked_cache_component(tmp_path: Path, component: str) -> None:
    cache, outside = tmp_path / "cache", _write(tmp_path / "outside", {"keep.txt": b"keep"})
    snapshot = _cache_with_symlinked(component, cache, outside)

    with pytest.raises(ModelIntegrityError, match="symlink"):
        model_integrity._purge(snapshot, cache, REPO)

    assert (outside / "keep.txt").read_bytes() == b"keep"


def test_refetch_never_deletes_through_a_symlinked_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, online: None
) -> None:
    cache = tmp_path / "cache"
    outside = _write(tmp_path / "outside", {**GOOD, "model.safetensors": b"tampered"})
    _snapshot(cache).parent.mkdir(parents=True)
    os.symlink(outside, _snapshot(cache))
    hub = _install(monkeypatch, FakeHub(GOOD))

    with pytest.raises(ModelIntegrityError, match="symlink"):
        fetch_pinned_snapshot(_pin(), cache_dir=cache)

    assert hub.calls == []
    assert (outside / "model.safetensors").read_bytes() == b"tampered"


class FailingApi(FakeApi):
    """``HfApi`` stand-in whose metadata call fails with ``error``."""

    def __init__(self, error: BaseException) -> None:
        super().__init__()
        self.error = error

    def model_info(self, repo_id: str, revision: str, files_metadata: bool):
        self.calls.append((repo_id, revision))
        raise self.error


def test_first_use_hub_metadata_failure_is_coded_and_leaves_the_snapshot_alone(
    cache: Path, pins_path: Path, monkeypatch: pytest.MonkeyPatch, online: None
) -> None:
    existing = {**GOOD, "model.safetensors": b"pre-existing"}
    snapshot = _write(_snapshot(cache), existing)
    hub = _install(monkeypatch, FakeHub(GOOD))
    api = _install_api(monkeypatch, FailingApi(ConnectionError("no route to host")))

    with pytest.raises(ModelIntegrityError) as exc:
        fetch_user_pinned_snapshot(REPO, REV, cache_dir=cache)

    assert exc.value.code == "model_integrity_failed"
    assert exc.value.reason == "Hub file metadata could not be fetched (ConnectionError)"
    assert api.calls == [(REPO, REV)]
    assert hub.calls == []
    for rel, data in existing.items():
        assert (snapshot / rel).read_bytes() == data
    assert not pins_path.exists()


def test_first_use_local_entry_not_found_from_the_hub_is_wrapped(
    cache: Path, pins_path: Path, monkeypatch: pytest.MonkeyPatch, online: None
) -> None:
    hub = _install(monkeypatch, FakeHub(GOOD))
    _install_api(monkeypatch, FailingApi(LocalEntryNotFoundError("offline cache miss")))

    with pytest.raises(ModelIntegrityError) as exc:
        fetch_user_pinned_snapshot(REPO, REV, cache_dir=cache)

    assert exc.value.code == "model_integrity_failed"
    assert exc.value.reason == "Hub file metadata could not be fetched (LocalEntryNotFoundError)"
    assert isinstance(exc.value.__cause__, LocalEntryNotFoundError)
    assert hub.calls == []
    assert not pins_path.exists()


def test_first_use_refuses_a_symlinked_snapshot_without_touching_its_target(
    tmp_path: Path, cache: Path, pins_path: Path, monkeypatch: pytest.MonkeyPatch, online: None
) -> None:
    outside = _write(tmp_path / "outside", {**GOOD, "model.safetensors": b"outside-bytes"})
    _snapshot(cache).parent.mkdir(parents=True)
    os.symlink(outside, _snapshot(cache))
    hub = _install(monkeypatch, FakeHub(GOOD))
    _install_api(monkeypatch, FakeApi())

    with pytest.raises(ModelIntegrityError, match="symlink") as exc:
        fetch_user_pinned_snapshot(REPO, REV, cache_dir=cache)

    assert exc.value.code == "model_integrity_failed"
    assert hub.calls == []
    assert (outside / "model.safetensors").read_bytes() == b"outside-bytes"
    assert (outside / "config.json").read_bytes() == GOOD["config.json"]
    assert not pins_path.exists()


# --- planted file names cannot forge reasons or log lines --------------------

_FORGED = "x.json\nWARNING forged log line"


def test_newline_in_a_planted_name_is_escaped_in_the_reason(tmp_path: Path) -> None:
    root = _write(tmp_path / "m", {**GOOD, _FORGED: b"planted"})

    with pytest.raises(ModelIntegrityError) as exc:
        verify_files(root, _pin())

    assert "\n" not in exc.value.reason and "\n" not in str(exc.value)
    assert "WARNING forged log line" in exc.value.reason


def test_newline_in_a_planted_name_does_not_reach_the_log_raw(
    cache: Path, monkeypatch: pytest.MonkeyPatch, online: None, caplog: pytest.LogCaptureFixture
) -> None:
    _write(_snapshot(cache), {**GOOD, _FORGED: b"planted"})
    _install(monkeypatch, FakeHub(GOOD))

    with caplog.at_level(logging.WARNING, logger=model_integrity.logger.name):
        fetch_pinned_snapshot(_pin(), cache_dir=cache)

    messages = [r.getMessage() for r in caplog.records if r.name == model_integrity.logger.name]
    assert messages
    assert all("\n" not in m for m in messages)
