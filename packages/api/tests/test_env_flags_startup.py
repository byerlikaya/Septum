"""Typed ``*_DEFAULT`` env flags are validated at API startup, not during seeding."""

from __future__ import annotations

import pytest
from septum_api import database, main
from septum_api.env_flags import (
    _INT_MAX,
    BOOL_DEFAULT_FLAGS,
    FLOAT_DEFAULT_FLAGS,
    INT_DEFAULT_FLAGS,
    EnvFlagError,
    parse_float_default_flags,
    parse_int_default_flags,
)
from septum_api.services import auth as auth_service

BAD_VALUE = "maybe-xq7"


@pytest.fixture
def _quiet_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth_service, "warm_dummy_password_hash", lambda: None)
    monkeypatch.setattr(main.bootstrap, "needs_setup", lambda: True)


def test_flag_registry_names_every_bool_default() -> None:
    names = {name for name, _ in BOOL_DEFAULT_FLAGS}
    assert "REQUIRE_APPROVAL_DEFAULT" in names
    assert "USE_GATEWAY_DEFAULT" in names
    assert len(names) == len(BOOL_DEFAULT_FLAGS)


@pytest.mark.parametrize("flag", [name for name, _ in BOOL_DEFAULT_FLAGS if name != "USE_GATEWAY_DEFAULT"])
async def test_invalid_non_gateway_flag_refuses_startup_naming_variable(
    monkeypatch: pytest.MonkeyPatch, _quiet_startup: None, flag: str
) -> None:
    monkeypatch.setenv(flag, BAD_VALUE)

    with pytest.raises(EnvFlagError) as excinfo:
        async with main.lifespan(main.app):
            pass

    message = str(excinfo.value)
    assert flag in message
    assert BAD_VALUE not in message


async def test_invalid_non_gateway_flag_stops_before_gateway_client_install(
    monkeypatch: pytest.MonkeyPatch, _quiet_startup: None
) -> None:
    installs: list[str] = []

    async def _spy() -> None:
        installs.append("called")

    monkeypatch.setattr(main, "_install_gateway_client", _spy)
    monkeypatch.setenv("REQUIRE_APPROVAL_DEFAULT", BAD_VALUE)

    with pytest.raises(EnvFlagError, match="REQUIRE_APPROVAL_DEFAULT"):
        async with main.lifespan(main.app):
            pass

    assert installs == []


async def test_invalid_gateway_flag_still_refuses_startup(
    monkeypatch: pytest.MonkeyPatch, _quiet_startup: None
) -> None:
    monkeypatch.setenv("USE_GATEWAY_DEFAULT", BAD_VALUE)

    with pytest.raises(EnvFlagError) as excinfo:
        async with main.lifespan(main.app):
            pass

    assert "USE_GATEWAY_DEFAULT" in str(excinfo.value)
    assert BAD_VALUE not in str(excinfo.value)


@pytest.mark.parametrize("value", ["1", "true", "YES", " on ", "0", "False", "no", "OFF", ""])
async def test_valid_flag_values_start(
    monkeypatch: pytest.MonkeyPatch, _quiet_startup: None, value: str
) -> None:
    for name, _ in BOOL_DEFAULT_FLAGS:
        monkeypatch.setenv(name, "false" if name == "USE_GATEWAY_DEFAULT" else value)

    async with main.lifespan(main.app):
        pass


def test_seeding_defaults_use_the_same_flag_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REQUIRE_APPROVAL_DEFAULT", "off")
    monkeypatch.setenv("USE_GATEWAY_DEFAULT", "yes")

    settings = database.build_default_app_settings()

    assert settings.require_approval is False
    assert settings.use_gateway is True


def test_seeding_refuses_invalid_flag_naming_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHOW_JSON_OUTPUT_DEFAULT", BAD_VALUE)

    with pytest.raises(EnvFlagError) as excinfo:
        database.build_default_app_settings()

    assert "SHOW_JSON_OUTPUT_DEFAULT" in str(excinfo.value)
    assert BAD_VALUE not in str(excinfo.value)


INVALID_NUMBERS = ["abc", "1.5", "1e3", "--3", "nan", "inf"]


@pytest.mark.parametrize("flag", [name for name, *_ in INT_DEFAULT_FLAGS])
async def test_invalid_int_flag_refuses_startup_naming_variable(
    monkeypatch: pytest.MonkeyPatch, _quiet_startup: None, flag: str
) -> None:
    monkeypatch.setenv(flag, BAD_VALUE)

    with pytest.raises(EnvFlagError) as excinfo:
        async with main.lifespan(main.app):
            pass

    assert flag in str(excinfo.value)
    assert BAD_VALUE not in str(excinfo.value)


@pytest.mark.parametrize("flag", [name for name, *_ in FLOAT_DEFAULT_FLAGS])
async def test_invalid_float_flag_refuses_startup_naming_variable(
    monkeypatch: pytest.MonkeyPatch, _quiet_startup: None, flag: str
) -> None:
    monkeypatch.setenv(flag, BAD_VALUE)

    with pytest.raises(EnvFlagError) as excinfo:
        async with main.lifespan(main.app):
            pass

    assert flag in str(excinfo.value)
    assert BAD_VALUE not in str(excinfo.value)


@pytest.mark.parametrize("value", INVALID_NUMBERS)
def test_numeric_flags_reject_non_finite_or_fractional_values(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("CHUNK_SIZE_DEFAULT", value)

    with pytest.raises(EnvFlagError) as excinfo:
        parse_int_default_flags()

    assert "CHUNK_SIZE_DEFAULT" in str(excinfo.value)
    assert value not in str(excinfo.value)


@pytest.mark.parametrize("name,minimum", [("CHUNK_SIZE_DEFAULT", 1), ("CHUNK_OVERLAP_DEFAULT", 0)])
def test_int_flag_below_minimum_refuses_naming_variable(
    monkeypatch: pytest.MonkeyPatch, name: str, minimum: int
) -> None:
    monkeypatch.setenv(name, str(minimum - 1))

    with pytest.raises(EnvFlagError, match=name):
        parse_int_default_flags()


@pytest.mark.parametrize("value", ["1.5", "2"])
def test_rag_threshold_out_of_range_refuses_naming_variable(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("RAG_RELEVANCE_THRESHOLD_DEFAULT", value)

    with pytest.raises(EnvFlagError, match="RAG_RELEVANCE_THRESHOLD_DEFAULT"):
        parse_float_default_flags()


def test_blank_int_and_float_flags_keep_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, _, _, _ in INT_DEFAULT_FLAGS:
        monkeypatch.setenv(name, "   ")
    for name, _, _, _ in FLOAT_DEFAULT_FLAGS:
        monkeypatch.setenv(name, "")

    assert parse_int_default_flags() == {name: default for name, default, _, _ in INT_DEFAULT_FLAGS}
    assert parse_float_default_flags() == {name: default for name, default, _, _ in FLOAT_DEFAULT_FLAGS}


def test_valid_int_and_float_flags_are_used_by_seeding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPROVAL_TIMEOUT_SECONDS_DEFAULT", " 45 ")
    monkeypatch.setenv("CHUNK_OVERLAP_DEFAULT", "0")
    monkeypatch.setenv("RAG_RELEVANCE_THRESHOLD_DEFAULT", "0.5")

    settings = database.build_default_app_settings()

    assert settings.approval_timeout_seconds == 45
    assert settings.chunk_overlap == 0
    assert settings.rag_relevance_threshold == 0.5


async def test_numeric_flags_at_their_minimum_start(
    monkeypatch: pytest.MonkeyPatch, _quiet_startup: None
) -> None:
    for name, _, minimum, _ in INT_DEFAULT_FLAGS:
        monkeypatch.setenv(name, str(minimum))
    monkeypatch.setenv("RAG_RELEVANCE_THRESHOLD_DEFAULT", "0.0")

    async with main.lifespan(main.app):
        pass


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "-0.1", "1.1", "abc"])
def test_rag_threshold_rejects_non_finite_and_out_of_range(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("RAG_RELEVANCE_THRESHOLD_DEFAULT", value)

    with pytest.raises(EnvFlagError) as excinfo:
        parse_float_default_flags()

    assert "RAG_RELEVANCE_THRESHOLD_DEFAULT" in str(excinfo.value)
    assert value not in str(excinfo.value)


@pytest.mark.parametrize("value", ["0.0", "1.0"])
def test_rag_threshold_accepts_bounds(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("RAG_RELEVANCE_THRESHOLD_DEFAULT", value)

    assert parse_float_default_flags()["RAG_RELEVANCE_THRESHOLD_DEFAULT"] == float(value)


@pytest.mark.parametrize("value", [str(_INT_MAX + 1), "9" * 5000])
def test_int_flag_above_maximum_or_overlong_raises_env_flag_error(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("CHUNK_SIZE_DEFAULT", value)

    with pytest.raises(EnvFlagError) as excinfo:
        parse_int_default_flags()

    assert "CHUNK_SIZE_DEFAULT" in str(excinfo.value)
    assert value not in str(excinfo.value)


@pytest.mark.parametrize(
    "flag,value",
    [("CHUNK_SIZE_DEFAULT", "0"), ("RAG_RELEVANCE_THRESHOLD_DEFAULT", "nan")],
)
async def test_bad_numeric_flag_stops_before_gateway_client_install_when_gateway_on(
    monkeypatch: pytest.MonkeyPatch, _quiet_startup: None, flag: str, value: str
) -> None:
    installs: list[str] = []

    async def _spy(*args: object, **kwargs: object) -> None:
        installs.append("called")

    monkeypatch.setattr(main, "_install_gateway_client", _spy)
    monkeypatch.setenv("USE_GATEWAY_DEFAULT", "true")
    monkeypatch.setenv(flag, value)

    with pytest.raises(EnvFlagError, match=flag):
        async with main.lifespan(main.app):
            pass

    assert installs == []


@pytest.mark.parametrize(
    "flag,value",
    [("CHUNK_SIZE_DEFAULT", "0"), ("RAG_RELEVANCE_THRESHOLD_DEFAULT", "nan")],
)
def test_seeding_refuses_invalid_numeric_flag_naming_variable(
    monkeypatch: pytest.MonkeyPatch, flag: str, value: str
) -> None:
    monkeypatch.setenv(flag, value)

    with pytest.raises(EnvFlagError) as excinfo:
        database.build_default_app_settings()

    assert flag in str(excinfo.value)
    assert value not in str(excinfo.value)
