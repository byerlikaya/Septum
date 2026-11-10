"""Contextual coverage: active contextual types and the local model probe.

The network is mocked with respx; no request leaves the test process.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import AsyncIterator

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from septum_api.database import build_default_app_settings
from septum_api.models import Base
from septum_api.models.regulation import RegulationRuleset
from septum_api.services import contextual_coverage
from septum_api.services.contextual_coverage import (
    MAX_PROBE_BODY_BYTES,
    DefaultReason,
    active_contextual_types,
    probe_local_pii_model,
)

pytestmark = pytest.mark.respx(assert_all_called=False)

BASE = "http://localhost:11434"
TAGS = f"{BASE}/api/tags"


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'coverage.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(bind=engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


def _ruleset(rid: str, types: list[str], active: bool = True) -> RegulationRuleset:
    return RegulationRuleset(
        id=rid, display_name=rid, region="x", entity_types=types, is_builtin=False, is_active=active
    )


def _settings(**overrides):
    row = build_default_app_settings()
    row.ollama_base_url = BASE
    row.ollama_deanon_model = "llama3.2:3b"
    for name, value in overrides.items():
        setattr(row, name, value)
    return row


def _tags(*names: str) -> httpx.Response:
    return httpx.Response(200, json={"models": [{"name": n} for n in names]})


async def test_contextual_types_are_the_active_intersection(db: AsyncSession) -> None:
    db.add_all([
        _ruleset("a", ["PERSON_NAME", "DIAGNOSIS", "RELIGION"]),
        _ruleset("b", ["DIAGNOSIS", "DNA_PROFILE", "EMAIL_ADDRESS"]),
        _ruleset("inactive", ["POLITICAL_OPINION"], active=False),
    ])
    await db.commit()
    assert await active_contextual_types(db) == {"DIAGNOSIS", "RELIGION", "DNA_PROFILE"}


async def test_no_contextual_types_when_active_packs_have_none(db: AsyncSession) -> None:
    db.add_all([_ruleset("plain", ["PERSON_NAME", "EMAIL_ADDRESS"]), _ruleset("empty", [])])
    await db.commit()
    assert await active_contextual_types(db) == frozenset()


async def test_builtin_health_pack_lists_contextual_types(db: AsyncSession) -> None:
    from septum_api.seeds.regulations import builtin_regulations

    gdpr = next(r for r in builtin_regulations() if r.id == "gdpr")
    gdpr.is_active = True
    db.add(gdpr)
    await db.commit()
    assert {"DIAGNOSIS", "MEDICATION", "RELIGION"} <= await active_contextual_types(db)


_OVERSIZE_BODY = b'{"models": [], "pad": "' + b"x" * MAX_PROBE_BODY_BYTES + b'"}'
_NESTED_BODY = b"[" * 100_000 + b"]" * 100_000

PROBE_FAILURES = [
    (httpx.ReadTimeout("slow"), DefaultReason.PROBE_TIMEOUT),
    (httpx.ConnectError("down"), DefaultReason.PROBE_UNREACHABLE),
    (httpx.Response(503), DefaultReason.PROBE_HTTP_ERROR),
    (httpx.Response(200, content=b"not json"), DefaultReason.PROBE_INVALID_RESPONSE),
    (httpx.Response(200, json=["x"]), DefaultReason.PROBE_INVALID_RESPONSE),
    (httpx.Response(200, json={"models": "x"}), DefaultReason.PROBE_INVALID_RESPONSE),
    (httpx.Response(200, json={}), DefaultReason.PROBE_INVALID_RESPONSE),
    (httpx.Response(200, content=_OVERSIZE_BODY), DefaultReason.PROBE_INVALID_RESPONSE),
    (
        httpx.Response(
            200, headers={"content-length": str(MAX_PROBE_BODY_BYTES + 1)}, content=b"{}"
        ),
        DefaultReason.PROBE_INVALID_RESPONSE,
    ),
    (httpx.Response(200, content=_NESTED_BODY), DefaultReason.PROBE_INVALID_RESPONSE),
    (_tags("other:7b"), DefaultReason.PROBE_MODEL_MISSING),
]


def _mock_effect(mock, effect) -> None:
    if isinstance(effect, Exception):
        mock.mock(side_effect=effect)
    else:
        mock.mock(return_value=effect)


async def test_probe_ok_exact_and_base_name(respx_mock) -> None:
    respx_mock.get(TAGS).mock(return_value=_tags("llama3.2:3b"))
    assert await probe_local_pii_model(_settings()) is DefaultReason.ENABLED
    assert await probe_local_pii_model(_settings(ollama_deanon_model="llama3.2")) is DefaultReason.ENABLED


async def test_probe_empty_model_name_is_missing(respx_mock) -> None:
    respx_mock.get(TAGS).mock(return_value=_tags("llama3.2:3b"))
    result = await probe_local_pii_model(_settings(ollama_deanon_model=""))
    assert result is DefaultReason.PROBE_MODEL_MISSING


@pytest.mark.parametrize(("effect", "expected"), PROBE_FAILURES)
async def test_probe_failure_reason(respx_mock, effect, expected: DefaultReason) -> None:
    _mock_effect(respx_mock.get(TAGS), effect)
    assert await probe_local_pii_model(_settings()) is expected


async def test_probe_refuses_disallowed_url_without_a_request(respx_mock) -> None:
    route = respx_mock.route().mock(return_value=_tags("llama3.2:3b"))
    result = await probe_local_pii_model(_settings(ollama_base_url="http://169.254.169.254"))
    assert result is DefaultReason.PROBE_URL_NOT_ALLOWED
    assert not route.called


async def test_probe_overall_deadline_maps_to_timeout(
    respx_mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return _tags("llama3.2:3b")

    monkeypatch.setattr(contextual_coverage, "PROBE_TIMEOUT_SECONDS", 0.05)
    respx_mock.get(TAGS).mock(side_effect=_slow)
    assert await probe_local_pii_model(_settings()) is DefaultReason.PROBE_TIMEOUT


async def test_probe_logs_nothing_with_url_or_model(
    respx_mock, caplog: pytest.LogCaptureFixture
) -> None:
    respx_mock.get(TAGS).mock(side_effect=httpx.ConnectError("down"))
    with caplog.at_level(logging.DEBUG):
        await probe_local_pii_model(_settings())
    assert "11434" not in caplog.text
    assert "llama3.2" not in caplog.text


@pytest.mark.parametrize(
    "effect",
    [PROBE_FAILURES[i][0] for i in (0, 1, 2, 3, 10)],
)
async def test_probe_failures_log_no_exception_text(
    respx_mock, caplog: pytest.LogCaptureFixture, effect
) -> None:
    _mock_effect(respx_mock.get(TAGS), effect)
    with caplog.at_level(logging.DEBUG):
        await probe_local_pii_model(_settings())
    assert all(r.exc_info is None for r in caplog.records)
    assert "Traceback" not in caplog.text
    for text in ("slow", "down", "other:7b", "not json"):
        assert text not in caplog.text
