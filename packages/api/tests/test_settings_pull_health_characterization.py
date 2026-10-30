"""Characterization tests for the Ollama pull stream and the ingestion health check.

Pins the exact SSE frames, upstream request, log lines and refusals of
``ollama_pull_endpoint`` and every status/message combination of
``get_ingestion_health_endpoint`` so both can be restructured without
changing what the Settings UI receives. The Ollama HTTP client, the
settings row, ``ffmpeg`` lookup and the ``whisper`` package are faked.
"""

from __future__ import annotations

import logging
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import HTTPException
from septum_api.errors import CodedHTTPError
from septum_api.routers import settings as settings_router

_LOGGER = settings_router.logger.name

FAILED = 'data: {"status": "Model pull failed.", "percent": 0, "done": true, "error": true}\n\n'


def _downloading(pct: int) -> str:
    return f'data: {{"status": "downloading", "percent": {pct}, "done": false}}\n\n'


SUCCESS = 'data: {"status": "success", "percent": 100, "done": true}\n\n'


class _Resp:
    def __init__(self, status_code: int, lines: list[Any]) -> None:
        self.status_code = status_code
        self._lines = lines

    async def aiter_lines(self):
        for line in self._lines:
            if isinstance(line, BaseException):
                raise line
            yield line


class _StreamCm:
    def __init__(self, resp: _Resp | BaseException) -> None:
        self.resp = resp

    async def __aenter__(self) -> _Resp:
        if isinstance(self.resp, BaseException):
            raise self.resp
        return self.resp

    async def __aexit__(self, *_exc: Any) -> None:
        return None


class _Client:
    def __init__(self, resp: _Resp | BaseException) -> None:
        self.resp = resp
        self.requests: list[tuple[str, str, Any]] = []
        self.timeouts: list[Any] = []

    def stream(self, method: str, url: str, json: Any = None) -> _StreamCm:
        self.requests.append((method, url, json))
        return _StreamCm(self.resp)

    async def __aenter__(self) -> "_Client":
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None


def _install(monkeypatch, resp, stored_url: str | None = "http://localhost:11434/") -> _Client:
    client = _Client(resp)

    def factory(timeout):
        client.timeouts.append(timeout)
        return client

    async def fake_settings(db):
        return SimpleNamespace(ollama_base_url=stored_url)

    monkeypatch.setattr(settings_router, "ollama_async_http_client", factory)
    monkeypatch.setattr(settings_router, "load_settings", fake_settings)
    return client


async def _pull(model: str = "llama3", base_url: str | None = None) -> list[str]:
    payload = settings_router.OllamaPullRequest(model=model, base_url=base_url)
    response = await settings_router.ollama_pull_endpoint(payload=payload, db=None, _user=None)
    assert response.media_type == "text/event-stream"
    return [frame async for frame in response.body_iterator]


def _logs(caplog) -> list[tuple[int, str]]:
    return [(r.levelno, r.getMessage()) for r in caplog.records if r.name == _LOGGER]


async def test_pull_requires_a_configured_url(monkeypatch):
    _install(monkeypatch, _Resp(200, []), stored_url="")
    with pytest.raises(HTTPException) as info:
        await _pull()
    assert (info.value.status_code, info.value.detail) == (400, "Ollama base URL not configured.")


async def test_pull_refuses_disallowed_url(monkeypatch):
    client = _install(monkeypatch, _Resp(200, []))
    with pytest.raises(CodedHTTPError) as info:
        await _pull(base_url="http://8.8.8.8:11434")
    assert info.value.status_code == 400
    assert client.requests == []


async def test_pull_streams_progress_until_success(monkeypatch, caplog):
    client = _install(
        monkeypatch,
        _Resp(
            200,
            [
                "",
                "   ",
                "not json",
                "[1, 2]",
                '"text"',
                '{"status": "pulling manifest"}',
                '{"status": "downloading", "total": 0, "completed": 5}',
                '{"status": "downloading", "total": null, "completed": 5}',
                '{"status": "downloading", "total": 7, "completed": null}',
                '{"status": "downloading", "total": 7, "completed": 3}',
                '{"status": "downloading", "total": 3, "completed": 3}',
                '{"status": "success", "total": 1}',
                '{"status": "downloading", "total": 2, "completed": 1}',
            ],
        ),
    )
    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        frames = await _pull()
    assert frames == [
        _downloading(0),
        _downloading(0),
        _downloading(0),
        _downloading(0),
        _downloading(42),
        _downloading(100),
        SUCCESS,
    ]
    assert client.requests == [
        ("POST", "http://localhost:11434/api/pull", {"name": "llama3", "stream": True})
    ]
    assert client.timeouts == [httpx.Timeout(connect=10.0, read=600.0, write=10.0, pool=10.0)]
    assert _logs(caplog) == []


async def test_pull_payload_url_wins_over_stored(monkeypatch):
    client = _install(monkeypatch, _Resp(200, []), stored_url="http://ollama:11434")
    assert await _pull(model="m:1", base_url="http://127.0.0.1:11434") == []
    assert client.requests == [
        ("POST", "http://127.0.0.1:11434/api/pull", {"name": "m:1", "stream": True})
    ]


@pytest.mark.parametrize("code", [400, 404, 500])
async def test_pull_rejected_status_yields_failure(monkeypatch, caplog, code):
    _install(monkeypatch, _Resp(code, ['{"status": "success"}']))
    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        assert await _pull() == [FAILED]
    assert _logs(caplog) == [(logging.WARNING, f"Ollama pull rejected: http_status={code}")]


async def test_pull_status_399_is_not_rejected(monkeypatch):
    _install(monkeypatch, _Resp(399, ['{"status": "success"}']))
    assert await _pull() == [SUCCESS]


async def test_pull_upstream_error_stops_stream(monkeypatch, caplog):
    _install(
        monkeypatch,
        _Resp(
            200,
            [
                '{"total": 4, "completed": 1}',
                '{"error": "secret upstream text"}',
                '{"status": "success"}',
            ],
        ),
    )
    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        assert await _pull() == [_downloading(25), FAILED]
    assert _logs(caplog) == [(logging.WARNING, "Ollama pull reported an error")]


async def test_pull_falsy_error_field_is_ignored(monkeypatch):
    _install(monkeypatch, _Resp(200, ['{"error": "", "status": "success"}']))
    assert await _pull() == [SUCCESS]


@pytest.mark.parametrize(
    ("resp", "exc_name", "prefix"),
    [
        (_Resp(200, ['{"total": 2, "completed": 1}', RuntimeError("secret")]), "RuntimeError", [_downloading(50)]),
        (httpx.ConnectError("secret host"), "ConnectError", []),
        (_Resp(200, ['{"total": 2, "completed": "x"}']), "TypeError", []),
    ],
)
async def test_pull_exceptions_yield_generic_failure(monkeypatch, caplog, resp, exc_name, prefix):
    _install(monkeypatch, resp)
    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        assert await _pull() == [*prefix, FAILED]
    assert _logs(caplog) == [(logging.WARNING, f"Ollama pull failed: {exc_name}")]


async def test_pull_stream_end_without_success_just_stops(monkeypatch):
    _install(monkeypatch, _Resp(200, ['{"total": 10, "completed": 1}']))
    assert await _pull() == [_downloading(10)]


# ---------------------------------------------------------------- ingestion health


@pytest.fixture
def health_env(monkeypatch, tmp_path):
    state: dict[str, Any] = {"whisper_model": "small"}

    async def fake_settings(db):
        return SimpleNamespace(whisper_model=state["whisper_model"])

    monkeypatch.setattr(settings_router, "load_settings", fake_settings)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(settings_router.shutil, "which", lambda name: f"/usr/bin/{name}")
    return state


def _set_whisper(monkeypatch, module: Any) -> None:
    monkeypatch.setitem(sys.modules, "whisper", module)


def _fake_whisper(models: Any = None) -> types.ModuleType:
    module = types.ModuleType("whisper")
    if models is not None:
        module._MODELS = models
    return module


async def _health() -> dict[str, Any]:
    result = await settings_router.get_ingestion_health_endpoint(db=None, _user=None)
    return result.model_dump()


async def test_health_whisper_and_ffmpeg_missing(health_env, monkeypatch):
    _set_whisper(monkeypatch, None)
    monkeypatch.setattr(settings_router.shutil, "which", lambda name: None)
    assert await _health() == {
        "ffmpeg": "missing",
        "whisper_package": "missing",
        "whisper_model": "missing",
        "message": (
            "Python package 'openai-whisper' is not installed in the backend environment. "
            "The 'ffmpeg' binary was not found on PATH."
        ),
    }


async def test_health_whisper_missing_ffmpeg_ok(health_env, monkeypatch):
    _set_whisper(monkeypatch, None)
    assert await _health() == {
        "ffmpeg": "ok",
        "whisper_package": "missing",
        "whisper_model": "missing",
        "message": "Python package 'openai-whisper' is not installed in the backend environment.",
    }


async def test_health_all_ok_uses_model_url_basename(health_env, monkeypatch, tmp_path):
    _set_whisper(monkeypatch, _fake_whisper({"small": "https://host/abc/small-v2.pt"}))
    cache = tmp_path / "xdg" / "whisper"
    cache.mkdir(parents=True)
    (cache / "small-v2.pt").write_bytes(b"x")
    assert await _health() == {
        "ffmpeg": "ok",
        "whisper_package": "ok",
        "whisper_model": "ok",
        "message": None,
    }


@pytest.mark.parametrize(
    "models",
    [None, {}, {"small": ""}, {"small": 42}, ["not", "a", "dict"]],
)
async def test_health_model_filename_falls_back_to_name(health_env, monkeypatch, tmp_path, models):
    _set_whisper(monkeypatch, _fake_whisper(models))
    expected = tmp_path / "xdg" / "whisper" / "small.pt"
    assert await _health() == {
        "ffmpeg": "ok",
        "whisper_package": "ok",
        "whisper_model": "missing",
        "message": f"Whisper model 'small' has not been downloaded yet (expected at '{expected}').",
    }
    expected.parent.mkdir(parents=True)
    expected.write_bytes(b"x")
    assert (await _health())["whisper_model"] == "ok"


@pytest.mark.parametrize(("stored", "name"), [(None, "base"), ("", "base"), ("  tiny  ", "tiny")])
async def test_health_model_name_default_and_trim(health_env, monkeypatch, tmp_path, stored, name):
    health_env["whisper_model"] = stored
    _set_whisper(monkeypatch, _fake_whisper({}))
    monkeypatch.setattr(settings_router.shutil, "which", lambda name: None)
    expected = tmp_path / "xdg" / "whisper" / f"{name}.pt"
    assert await _health() == {
        "ffmpeg": "missing",
        "whisper_package": "ok",
        "whisper_model": "missing",
        "message": (
            f"Whisper model '{name}' has not been downloaded yet (expected at '{expected}'). "
            "The 'ffmpeg' binary was not found on PATH."
        ),
    }


async def test_health_without_xdg_uses_home_cache(health_env, monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CACHE_HOME")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _set_whisper(monkeypatch, _fake_whisper({}))
    expected = Path(str(tmp_path / "home")) / ".cache" / "whisper" / "small.pt"
    result = await _health()
    assert result["message"] == (
        f"Whisper model 'small' has not been downloaded yet (expected at '{expected}')."
    )


async def test_health_empty_xdg_uses_home_cache(health_env, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", "")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _set_whisper(monkeypatch, _fake_whisper({}))
    expected = tmp_path / "home" / ".cache" / "whisper" / "small.pt"
    assert str(expected) in (await _health())["message"]
