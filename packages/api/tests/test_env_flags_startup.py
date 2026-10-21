"""Boolean ``*_DEFAULT`` env flags are validated at API startup, not during seeding."""

from __future__ import annotations

import pytest
from septum_api import database, main
from septum_api.env_flags import BOOL_DEFAULT_FLAGS, EnvFlagError
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
