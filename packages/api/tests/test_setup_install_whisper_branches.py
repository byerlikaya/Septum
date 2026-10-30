"""Characterization of POST /api/setup/install-whisper branches (pins behaviour before a refactor)."""

from __future__ import annotations

import hashlib
import json
import sys
import types

import pytest
from fastapi.testclient import TestClient
from septum_api.main import app
from septum_api.routers import setup as setup_router
from septum_api.services.whisper_models import WhisperDownloadError, WhisperInstallBusy
from septum_api.utils.auth_dependency import require_admin_or_bootstrap

client = TestClient(app)

URL = f"https://models.invalid/whisper/{hashlib.sha256(b'weights').hexdigest()}/base.pt"


@pytest.fixture(autouse=True)
def _open_wizard():
    app.dependency_overrides[require_admin_or_bootstrap] = lambda: None
    yield
    app.dependency_overrides.pop(require_admin_or_bootstrap, None)


@pytest.fixture
def fake_whisper(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = types.ModuleType("whisper")
    fake._MODELS = {"base": URL}
    monkeypatch.setitem(sys.modules, "whisper", fake)


@pytest.fixture
def installs(monkeypatch: pytest.MonkeyPatch) -> list:
    """Replace the verified installer; records each target it is asked to install."""
    seen: list = []

    def _install(target, progress=None, *, wait=True):
        seen.append(target)

    monkeypatch.setattr(setup_router, "install_whisper_file", _install)
    return seen


def _events(resp) -> list[dict]:
    return [
        json.loads(line[len("data: "):])
        for line in resp.text.splitlines()
        if line.startswith("data: ")
    ]


def test_missing_whisper_package_answers_with_a_json_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "whisper", None)

    resp = client.post("/api/setup/install-whisper", json={"model": "base"})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert body["ok"] is False
    assert body["message"] == "openai-whisper package not installed"


def test_blank_model_name_defaults_to_base(fake_whisper, installs: list) -> None:
    resp = client.post("/api/setup/install-whisper", json={"model": "   "})

    assert resp.status_code == 200
    assert [t.url for t in installs] == [URL]
    assert _events(resp)[-1] == {"percent": 100, "status": "ready", "done": True}


def test_surrounding_whitespace_is_stripped_from_the_model_name(fake_whisper, installs: list) -> None:
    resp = client.post("/api/setup/install-whisper", json={"model": "  base  "})

    assert resp.status_code == 200
    assert len(installs) == 1


def test_resolution_failure_after_registry_check_streams_one_error_event(
    fake_whisper, installs: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _refuse(models_map, model_name):
        raise WhisperDownloadError("Unknown Whisper model")

    monkeypatch.setattr(setup_router, "resolve_whisper_target", _refuse)

    resp = client.post("/api/setup/install-whisper", json={"model": "base"})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert _events(resp) == [{"percent": 0, "status": "Unknown Whisper model", "error": True}]
    assert installs == []


def test_ordinary_install_failure_streams_a_generic_error_event(
    fake_whisper, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail(target, progress=None, *, wait=True):
        raise WhisperDownloadError("Model download URL must use https")

    monkeypatch.setattr(setup_router, "install_whisper_file", _fail)

    resp = client.post("/api/setup/install-whisper", json={"model": "base"})

    events = _events(resp)
    assert events[0] == {"percent": 0, "status": "downloading"}
    assert events[-1].get("error") is True
    assert events[-1]["percent"] == 0
    assert "done" not in events[-1]


def test_busy_install_reports_waiting_then_finishes(
    fake_whisper, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts: list[int] = []

    def _busy_once(target, progress=None, *, wait=True):
        attempts.append(1)
        if len(attempts) == 1:
            raise WhisperInstallBusy("busy")

    monkeypatch.setattr(setup_router, "install_whisper_file", _busy_once)

    resp = client.post("/api/setup/install-whisper", json={"model": "base"})

    statuses = [event["status"] for event in _events(resp)]
    assert statuses[0] == "downloading"
    assert "waiting" in statuses
    assert _events(resp)[-1] == {"percent": 100, "status": "ready", "done": True}
    assert len(attempts) == 2
