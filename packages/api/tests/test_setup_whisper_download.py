"""Tests for the Whisper model download in the setup wizard router (offline)."""

from __future__ import annotations

import fcntl
import hashlib
import json
import sys
import threading
import time
import types
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from septum_api.bootstrap import _invalidate_cache
from septum_api.main import app
from septum_api.services import whisper_models
from septum_api.utils.auth_dependency import require_admin_or_bootstrap

client = TestClient(app)

PAYLOAD = b"fake-whisper-weights" * 64
PAYLOAD_SHA = hashlib.sha256(PAYLOAD).hexdigest()
WRONG_SHA = hashlib.sha256(b"something else").hexdigest()


@pytest.fixture(autouse=True)
def _fresh_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SEPTUM_CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    for env_var in (
        "DATABASE_URL", "DB_PATH", "REDIS_URL", "ENCRYPTION_KEY",
        "JWT_SECRET_KEY", "JWT_EXPIRATION_MINUTES", "LOG_LEVEL",
        "RATE_LIMIT_DEFAULT", "FRONTEND_ORIGIN",
    ):
        monkeypatch.delenv(env_var, raising=False)
    _invalidate_cache()
    yield
    _invalidate_cache()


@pytest.fixture
def real_auth() -> None:
    """Request this fixture to run against the real bootstrap/admin dependency."""


@pytest.fixture(autouse=True)
def _wizard_access(request: pytest.FixtureRequest):
    """Grant the bootstrap/admin dependency, except for tests that exercise it."""
    if "real_auth" in request.fixturenames:
        yield
        return
    app.dependency_overrides[require_admin_or_bootstrap] = lambda: None
    yield
    app.dependency_overrides.pop(require_admin_or_bootstrap, None)


class _FakeStream:
    def __init__(
        self,
        payload: bytes,
        url: str,
        *,
        final_url: str | None = None,
        content_length: int | None = None,
        send_length: bool = True,
        drip_bytes: int = 256,
        drip_delay: float = 0.0,
    ) -> None:
        declared = len(payload) if content_length is None else content_length
        self.headers = {"Content-Length": str(declared)} if send_length else {}
        self._payload = payload
        self.url = httpx.URL(final_url or url)
        self.history = [httpx.Response(302, request=httpx.Request("GET", url))] if final_url else []
        self.drip_bytes = drip_bytes
        self.drip_delay = drip_delay

    def __enter__(self) -> "_FakeStream":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_raw(self, chunk_size: int | None = None):
        """Like httpx: ``None`` yields data as it arrives (here one drip at a time)."""
        step = chunk_size or self.drip_bytes
        for i in range(0, len(self._payload), step):
            if self.drip_delay:
                time.sleep(self.drip_delay)
            yield self._payload[i : i + step]


@pytest.fixture
def stream_options() -> dict:
    """Per-test overrides for the fake response (payload, redirect, length)."""
    return {}


@pytest.fixture
def request_headers() -> list[dict]:
    """Headers of every faked model request."""
    return []


@pytest.fixture
def http_calls(
    monkeypatch: pytest.MonkeyPatch, stream_options: dict, request_headers: list[dict]
) -> list[str]:
    """Replace httpx.stream with an in-memory fake; records requested URLs."""
    calls: list[str] = []

    def fake_stream(method: str, url: str, **kwargs: object) -> _FakeStream:
        calls.append(url)
        request_headers.append(dict(kwargs.get("headers") or {}))
        options = dict(stream_options)
        payload = options.pop("payload", PAYLOAD)
        return _FakeStream(payload, url, **options)

    monkeypatch.setattr(httpx, "stream", fake_stream)
    return calls


def _install_fake_whisper(monkeypatch: pytest.MonkeyPatch, url: str) -> list[str]:
    """Register a fake ``whisper`` module; returns the names passed to ``load_model``."""
    loaded: list[str] = []
    fake = types.ModuleType("whisper")
    fake._MODELS = {"base": url}
    fake.load_model = lambda name, device=None: loaded.append(name)
    monkeypatch.setitem(sys.modules, "whisper", fake)
    return loaded


def _events(resp) -> list[dict]:
    return [
        json.loads(line[len("data: "):])
        for line in resp.text.splitlines()
        if line.startswith("data: ")
    ]


def _cached(tmp_path: Path) -> list[Path]:
    """Model and temp files in the cache; the sidecar install lock is not a model file."""
    cache = tmp_path / "cache" / "whisper"
    return [p for p in cache.iterdir() if p.suffix != ".lock"] if cache.exists() else []


def test_rejects_non_https_url_without_any_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, http_calls: list[str]
):
    url = f"http://models.invalid/whisper/{PAYLOAD_SHA}/base.pt"
    _install_fake_whisper(monkeypatch, url)

    resp = client.post("/api/setup/install-whisper", json={"model": "base"})

    assert resp.status_code == 200
    final = _events(resp)[-1]
    assert final.get("error") is True
    assert "https" in final["status"]
    assert http_calls == []
    assert _cached(tmp_path) == []


def test_sha256_mismatch_reports_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, http_calls: list[str]):
    url = f"https://models.invalid/whisper/{WRONG_SHA}/base.pt"
    _install_fake_whisper(monkeypatch, url)

    resp = client.post("/api/setup/install-whisper", json={"model": "base"})

    final = _events(resp)[-1]
    assert final.get("error") is True
    assert "checksum" in final["status"].lower()
    assert final.get("done") is not True


def test_sha256_mismatch_leaves_no_usable_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, http_calls: list[str]
):
    url = f"https://models.invalid/whisper/{WRONG_SHA}/base.pt"
    _install_fake_whisper(monkeypatch, url)

    client.post("/api/setup/install-whisper", json={"model": "base"})

    assert _cached(tmp_path) == []

    retry = client.post("/api/setup/install-whisper", json={"model": "base"})
    assert _events(retry)[-1].get("done") is not True


def test_valid_checksum_stores_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, http_calls: list[str]):
    url = f"https://models.invalid/whisper/{PAYLOAD_SHA}/base.pt"
    _install_fake_whisper(monkeypatch, url)

    resp = client.post("/api/setup/install-whisper", json={"model": "base"})

    final = _events(resp)[-1]
    assert final.get("done") is True
    assert final.get("error") is not True
    stored = _cached(tmp_path)
    assert [p.name for p in stored] == ["base.pt"]
    assert stored[0].read_bytes() == PAYLOAD


def test_redirect_to_http_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, http_calls: list[str], stream_options: dict
):
    url = f"https://models.invalid/whisper/{PAYLOAD_SHA}/base.pt"
    stream_options["final_url"] = f"http://mirror.invalid/whisper/{PAYLOAD_SHA}/base.pt"
    _install_fake_whisper(monkeypatch, url)

    resp = client.post("/api/setup/install-whisper", json={"model": "base"})

    final = _events(resp)[-1]
    assert final.get("error") is True
    assert "https" in final["status"]
    assert _cached(tmp_path) == []


def test_redirect_within_https_is_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, http_calls: list[str], stream_options: dict
):
    url = f"https://models.invalid/whisper/{PAYLOAD_SHA}/base.pt"
    stream_options["final_url"] = f"https://mirror.invalid/whisper/{PAYLOAD_SHA}/base.pt"
    _install_fake_whisper(monkeypatch, url)

    resp = client.post("/api/setup/install-whisper", json={"model": "base"})

    assert _events(resp)[-1].get("done") is True
    assert [p.name for p in _cached(tmp_path)] == ["base.pt"]


def test_body_longer_than_content_length_is_aborted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, http_calls: list[str], stream_options: dict
):
    url = f"https://models.invalid/whisper/{PAYLOAD_SHA}/base.pt"
    stream_options["content_length"] = len(PAYLOAD) // 2
    _install_fake_whisper(monkeypatch, url)

    resp = client.post("/api/setup/install-whisper", json={"model": "base"})

    final = _events(resp)[-1]
    assert final.get("error") is True
    assert "size" in final["status"]
    assert _cached(tmp_path) == []


def test_declared_length_above_model_cap_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, http_calls: list[str], stream_options: dict
):
    url = f"https://models.invalid/whisper/{PAYLOAD_SHA}/base.pt"
    stream_options["content_length"] = 10_000_000_000
    _install_fake_whisper(monkeypatch, url)

    resp = client.post("/api/setup/install-whisper", json={"model": "base"})

    final = _events(resp)[-1]
    assert final.get("error") is True
    assert "size" in final["status"]
    assert _cached(tmp_path) == []


def test_body_above_model_cap_without_content_length_is_aborted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, http_calls: list[str], stream_options: dict
):
    monkeypatch.setitem(whisper_models.WHISPER_APPROX_BYTES, "base", len(PAYLOAD) // 4)
    url = f"https://models.invalid/whisper/{PAYLOAD_SHA}/base.pt"
    stream_options["send_length"] = False
    _install_fake_whisper(monkeypatch, url)

    resp = client.post("/api/setup/install-whisper", json={"model": "base"})

    final = _events(resp)[-1]
    assert final.get("error") is True
    assert "size" in final["status"]
    assert _cached(tmp_path) == []


def test_corrupt_cached_file_is_not_reported_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, http_calls: list[str]
):
    url = f"https://models.invalid/whisper/{PAYLOAD_SHA}/base.pt"
    _install_fake_whisper(monkeypatch, url)
    cache = tmp_path / "cache" / "whisper"
    cache.mkdir(parents=True)
    (cache / "base.pt").write_bytes(b"truncated")

    status = client.get("/api/setup/whisper-status", params={"model": "base"})
    assert status.json()["installed"] is False

    resp = client.post("/api/setup/install-whisper", json={"model": "base"})

    assert http_calls == [url]
    assert _events(resp)[-1].get("done") is True
    assert (cache / "base.pt").read_bytes() == PAYLOAD
    assert client.get("/api/setup/whisper-status", params={"model": "base"}).json()["installed"] is True


def test_verified_cached_file_skips_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, http_calls: list[str]
):
    url = f"https://models.invalid/whisper/{PAYLOAD_SHA}/base.pt"
    _install_fake_whisper(monkeypatch, url)
    cache = tmp_path / "cache" / "whisper"
    cache.mkdir(parents=True)
    (cache / "base.pt").write_bytes(PAYLOAD)

    resp = client.post("/api/setup/install-whisper", json={"model": "base"})

    assert _events(resp)[-1].get("done") is True
    assert http_calls == []


def test_non_https_url_is_rejected_before_cached_file_is_trusted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, http_calls: list[str]
):
    url = f"http://models.invalid/whisper/{PAYLOAD_SHA}/base.pt"
    _install_fake_whisper(monkeypatch, url)
    cache = tmp_path / "cache" / "whisper"
    cache.mkdir(parents=True)
    (cache / "base.pt").write_bytes(PAYLOAD)

    resp = client.post("/api/setup/install-whisper", json={"model": "base"})

    final = _events(resp)[-1]
    assert final.get("error") is True
    assert "https" in final["status"]
    assert client.get("/api/setup/whisper-status", params={"model": "base"}).json()["installed"] is False


def test_unknown_model_reports_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, http_calls: list[str]
):
    _install_fake_whisper(monkeypatch, f"https://models.invalid/whisper/{PAYLOAD_SHA}/base.pt")

    resp = client.post("/api/setup/install-whisper", json={"model": "no-such-model"})

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Whisper model is not in the supported model registry"
    assert "no-such-model" not in resp.text
    assert http_calls == []
    status = client.get("/api/setup/whisper-status", params={"model": "no-such-model"}).json()
    assert status["installed"] is False


def test_concurrent_installs_of_one_model_download_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    url = f"https://models.invalid/whisper/{PAYLOAD_SHA}/base.pt"
    target = whisper_models.resolve_whisper_target({"base": url}, "base")
    calls: list[str] = []
    gate = threading.Event()

    def slow_stream(method: str, stream_url: str, **_: object) -> _FakeStream:
        calls.append(stream_url)
        gate.wait(timeout=5)
        return _FakeStream(PAYLOAD, stream_url)

    monkeypatch.setattr(httpx, "stream", slow_stream)
    errors: list[BaseException] = []

    def run() -> None:
        try:
            whisper_models.install_whisper_file(target, {"downloaded": 0, "total": 0})
        except BaseException as exc:
            errors.append(exc)

    workers = [threading.Thread(target=run) for _ in range(3)]
    for w in workers:
        w.start()
    deadline = time.monotonic() + 5
    while not calls and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.2)
    gate.set()
    for w in workers:
        w.join(timeout=10)

    assert errors == []
    assert calls == [url]
    assert target.path.read_bytes() == PAYLOAD


def test_status_does_not_hash_the_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, http_calls: list[str]
):
    url = f"https://models.invalid/whisper/{PAYLOAD_SHA}/base.pt"
    _install_fake_whisper(monkeypatch, url)
    monkeypatch.setitem(whisper_models.WHISPER_APPROX_BYTES, "base", len(PAYLOAD))
    cache = tmp_path / "cache" / "whisper"
    cache.mkdir(parents=True)
    (cache / "base.pt").write_bytes(PAYLOAD)

    def _no_hashing(path: Path) -> str:
        raise AssertionError("status must not hash the model file")

    monkeypatch.setattr(whisper_models, "_sha256_file", _no_hashing)

    assert client.get("/api/setup/whisper-status", params={"model": "base"}).json()["installed"] is True


def test_overall_deadline_aborts_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, http_calls: list[str]
):
    url = f"https://models.invalid/whisper/{PAYLOAD_SHA}/base.pt"
    _install_fake_whisper(monkeypatch, url)
    monkeypatch.setattr(whisper_models, "_DEADLINE_BASE_SECONDS", -1_000_000.0)

    resp = client.post("/api/setup/install-whisper", json={"model": "base"})

    final = _events(resp)[-1]
    assert final.get("error") is True
    assert "timed out" in final["status"]
    assert _cached(tmp_path) == []


def test_temp_file_of_another_writer_is_left_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, http_calls: list[str]
):
    url = f"https://models.invalid/whisper/{PAYLOAD_SHA}/base.pt"
    _install_fake_whisper(monkeypatch, url)
    cache = tmp_path / "cache" / "whisper"
    cache.mkdir(parents=True)
    foreign = cache / ".base.pt.other-writer.part"
    foreign.write_bytes(b"in progress elsewhere")

    resp = client.post("/api/setup/install-whisper", json={"model": "base"})

    assert _events(resp)[-1].get("done") is True
    assert foreign.read_bytes() == b"in progress elsewhere"
    assert (cache / "base.pt").read_bytes() == PAYLOAD


def test_install_waits_for_the_cross_process_file_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, http_calls: list[str]
):
    url = f"https://models.invalid/whisper/{PAYLOAD_SHA}/base.pt"
    target = whisper_models.resolve_whisper_target({"base": url}, "base")
    target.path.parent.mkdir(parents=True)
    lock_path = target.path.with_name(target.path.name + ".lock")

    with open(lock_path, "a+b") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX)
        worker = threading.Thread(target=whisper_models.install_whisper_file, args=(target,))
        worker.start()
        time.sleep(0.3)
        assert http_calls == []
        fcntl.flock(held.fileno(), fcntl.LOCK_UN)
    worker.join(timeout=5)

    assert http_calls == [url]
    assert target.path.read_bytes() == PAYLOAD


def test_settings_install_goes_through_verified_installer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, http_calls: list[str]
):
    from septum_api.models.user import User
    from septum_api.routers import settings as settings_router

    url = f"https://models.invalid/whisper/{WRONG_SHA}/base.pt"
    loaded = _install_fake_whisper(monkeypatch, url)

    class _Settings:
        whisper_model = "base"

    async def _load_settings(db):
        return _Settings()

    monkeypatch.setattr(settings_router, "load_settings", _load_settings)
    import asyncio

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            settings_router.install_whisper_model_endpoint(db=None, _user=User(role="admin"))
        )

    assert "checksum" in excinfo.value.detail.lower()
    assert http_calls == [url]
    assert loaded == []
    assert _cached(tmp_path) == []


async def test_wizard_sequence_then_admin_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, http_calls: list[str], router_client, real_auth
):
    """initialize -> whisper-status -> install-whisper works; after setup only admins may call them."""
    from unittest.mock import AsyncMock, patch

    url = f"https://models.invalid/whisper/{PAYLOAD_SHA}/base.pt"
    _install_fake_whisper(monkeypatch, url)

    with patch("septum_api.routers.setup.engine_is_ready", return_value=False), \
         patch("septum_api.routers.setup.initialize_engine"), \
         patch("septum_api.routers.setup.init_db", new_callable=AsyncMock):
        init = await router_client.post("/api/setup/initialize", json={"database_type": "sqlite"})
    assert init.json()["ok"] is True
    _invalidate_cache()

    status = await router_client.get("/api/setup/whisper-status", params={"model": "base"})
    assert status.status_code == 200
    assert status.json()["installed"] is False
    install = await router_client.post("/api/setup/install-whisper", json={"model": "base"})
    assert install.status_code == 200
    assert _events(install)[-1].get("done") is True

    reg = await router_client.post(
        "/api/auth/register", json={"email": "root@example.com", "password": "password-12345"}
    )
    token = reg.json()["access_token"]

    anon_status = await router_client.get("/api/setup/whisper-status", params={"model": "base"})
    anon_install = await router_client.post("/api/setup/install-whisper", json={"model": "base"})
    assert anon_status.status_code in (401, 403)
    assert anon_install.status_code in (401, 403)

    auth = {"Authorization": f"Bearer {token}"}
    admin_status = await router_client.get(
        "/api/setup/whisper-status", params={"model": "base"}, headers=auth
    )
    assert admin_status.status_code == 200
    assert admin_status.json()["installed"] is True
    admin_install = await router_client.post(
        "/api/setup/install-whisper", json={"model": "base"}, headers=auth
    )
    assert _events(admin_install)[-1].get("done") is True
    assert http_calls == [url]


def test_slow_drip_hits_the_overall_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, http_calls: list[str], stream_options: dict
):
    url = f"https://models.invalid/whisper/{PAYLOAD_SHA}/base.pt"
    _install_fake_whisper(monkeypatch, url)
    stream_options.update(drip_bytes=1, drip_delay=0.005)
    monkeypatch.setattr(whisper_models.WhisperTarget, "deadline_seconds", property(lambda self: 0.3))

    resp = client.post("/api/setup/install-whisper", json={"model": "base"})

    final = _events(resp)[-1]
    assert final.get("error") is True
    assert "timed out" in final["status"]
    assert _cached(tmp_path) == []


def test_busy_lock_is_reported_without_blocking(tmp_path: Path, http_calls: list[str]):
    url = f"https://models.invalid/whisper/{PAYLOAD_SHA}/base.pt"
    target = whisper_models.resolve_whisper_target({"base": url}, "base")
    target.path.parent.mkdir(parents=True)
    lock_path = target.path.with_name(target.path.name + ".lock")

    with open(lock_path, "a+b") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX)
        started = time.monotonic()
        with pytest.raises(whisper_models.WhisperInstallBusy):
            whisper_models.install_whisper_file(target, wait=False)
        assert time.monotonic() - started < 1
        fcntl.flock(held.fileno(), fcntl.LOCK_UN)

    assert http_calls == []


def test_sse_waits_for_a_concurrent_install_then_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, http_calls: list[str]
):
    url = f"https://models.invalid/whisper/{PAYLOAD_SHA}/base.pt"
    _install_fake_whisper(monkeypatch, url)
    target = whisper_models.resolve_whisper_target({"base": url}, "base")
    target.path.parent.mkdir(parents=True)
    lock_path = target.path.with_name(target.path.name + ".lock")
    result: dict = {}

    def run() -> None:
        result["resp"] = client.post("/api/setup/install-whisper", json={"model": "base"})

    with open(lock_path, "a+b") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX)
        worker = threading.Thread(target=run)
        worker.start()
        time.sleep(1.5)
        assert http_calls == []
        fcntl.flock(held.fileno(), fcntl.LOCK_UN)
    worker.join(timeout=15)

    events = _events(result["resp"])
    assert any(e.get("status") == "waiting" for e in events)
    assert events[-1].get("done") is True
    assert http_calls == [url]


def test_stale_temp_files_are_swept_and_model_is_owner_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, http_calls: list[str]
):
    import os

    url = f"https://models.invalid/whisper/{PAYLOAD_SHA}/base.pt"
    _install_fake_whisper(monkeypatch, url)
    cache = tmp_path / "cache" / "whisper"
    cache.mkdir(parents=True)
    stale = cache / ".base.pt.crashed-writer.part"
    stale.write_bytes(b"half a model")
    old = time.time() - 10 * 24 * 3600
    os.utime(stale, (old, old))

    resp = client.post("/api/setup/install-whisper", json={"model": "base"})

    assert _events(resp)[-1].get("done") is True
    assert not stale.exists()
    assert ((cache / "base.pt").stat().st_mode & 0o777) == 0o600


def test_unknown_model_error_does_not_echo_the_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, http_calls: list[str]):
    _install_fake_whisper(monkeypatch, f"https://models.invalid/whisper/{PAYLOAD_SHA}/base.pt")

    resp = client.post("/api/setup/install-whisper", json={"model": "probe-<script>"})

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Whisper model is not in the supported model registry"
    assert "probe-" not in resp.text


def _hold_download_slot():
    assert whisper_models._download_slot.acquire(blocking=False)


def test_one_download_at_a_time_across_models(tmp_path: Path, http_calls: list[str]):
    url = f"https://models.invalid/whisper/{PAYLOAD_SHA}/small.pt"
    target = whisper_models.resolve_whisper_target({"small": url}, "small")
    _hold_download_slot()
    try:
        with pytest.raises(whisper_models.WhisperInstallBusy):
            whisper_models.install_whisper_file(target, wait=False)
    finally:
        whisper_models._download_slot.release()

    assert http_calls == []
    whisper_models.install_whisper_file(target, wait=False)
    assert http_calls == [url]


def test_verified_model_does_not_need_the_download_slot(tmp_path: Path, http_calls: list[str]):
    url = f"https://models.invalid/whisper/{PAYLOAD_SHA}/base.pt"
    target = whisper_models.resolve_whisper_target({"base": url}, "base")
    target.path.parent.mkdir(parents=True)
    target.path.write_bytes(PAYLOAD)
    _hold_download_slot()
    try:
        whisper_models.install_whisper_file(target, wait=False)
    finally:
        whisper_models._download_slot.release()

    assert http_calls == []


def test_sse_shows_waiting_while_another_model_downloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, http_calls: list[str]
):
    url = f"https://models.invalid/whisper/{PAYLOAD_SHA}/base.pt"
    _install_fake_whisper(monkeypatch, url)
    result: dict = {}

    def run() -> None:
        result["resp"] = client.post("/api/setup/install-whisper", json={"model": "base"})

    _hold_download_slot()
    try:
        worker = threading.Thread(target=run)
        worker.start()
        time.sleep(1.5)
        assert http_calls == []
    finally:
        whisper_models._download_slot.release()
    worker.join(timeout=15)

    events = _events(result["resp"])
    assert any(e.get("status") == "waiting" for e in events)
    assert events[-1].get("done") is True


def test_settings_install_reports_conflict_while_busy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, http_calls: list[str]
):
    import asyncio

    from fastapi import HTTPException
    from septum_api.models.user import User
    from septum_api.routers import settings as settings_router

    url = f"https://models.invalid/whisper/{PAYLOAD_SHA}/base.pt"
    loaded = _install_fake_whisper(monkeypatch, url)

    class _Settings:
        whisper_model = "base"

    async def _load_settings(db):
        return _Settings()

    monkeypatch.setattr(settings_router, "load_settings", _load_settings)
    _hold_download_slot()
    try:
        with pytest.raises(HTTPException) as excinfo:
            asyncio.run(
                settings_router.install_whisper_model_endpoint(db=None, _user=User(role="admin"))
            )
    finally:
        whisper_models._download_slot.release()

    assert excinfo.value.status_code == 409
    assert excinfo.value.detail == "Model install already in progress"
    assert http_calls == [] and loaded == []


def test_request_asks_for_identity_encoding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, http_calls: list[str], request_headers: list[dict]
):
    _install_fake_whisper(monkeypatch, f"https://models.invalid/whisper/{PAYLOAD_SHA}/base.pt")

    client.post("/api/setup/install-whisper", json={"model": "base"})

    assert request_headers == [{"Accept-Encoding": "identity"}]


@pytest.mark.parametrize("declared", ["\uff11\uff12\uff13", "\u00b2", "12a", "-1", "9" * 5000])
def test_non_ascii_or_malformed_content_length_is_refused(
    declared: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, http_calls: list[str]
):
    url = f"https://models.invalid/whisper/{PAYLOAD_SHA}/base.pt"
    target = whisper_models.resolve_whisper_target({"base": url}, "base")

    def fake_stream(method: str, stream_url: str, **_: object) -> _FakeStream:
        fake = _FakeStream(PAYLOAD, stream_url)
        fake.headers = {"Content-Length": declared}
        return fake

    monkeypatch.setattr(httpx, "stream", fake_stream)

    with pytest.raises(whisper_models.WhisperDownloadError, match="size"):
        whisper_models.install_whisper_file(target)
    assert not target.path.exists()


def test_ingestion_install_does_not_wait_for_the_download_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, http_calls: list[str]
):
    url = f"https://models.invalid/whisper/{PAYLOAD_SHA}/base.pt"
    loaded = _install_fake_whisper(monkeypatch, url)
    _hold_download_slot()
    try:
        worker = threading.Thread(target=whisper_models.ensure_whisper_model, args=("base",))
        worker.start()
        worker.join(timeout=5)
        assert not worker.is_alive()
    finally:
        whisper_models._download_slot.release()

    assert http_calls == [url]
    assert (tmp_path / "cache" / "whisper" / "base.pt").read_bytes() == PAYLOAD
    assert loaded == []
