"""Regression tests: env-less `get_settings()` defaults mirror the DB seed defaults."""

from __future__ import annotations

import pytest
from septum_api.config import get_settings
from septum_api.database import build_default_app_settings
from septum_api.models.settings import AppSettings


def test_require_approval_defaults_to_true_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REQUIRE_APPROVAL_DEFAULT", raising=False)
    assert get_settings().require_approval is True


def test_use_ollama_layer_defaults_to_true_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("USE_OLLAMA_LAYER_DEFAULT", raising=False)
    assert get_settings().use_ollama_layer is True


def test_llm_model_default_matches_seed_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_MODEL", raising=False)
    assert get_settings().llm_model == "claude-sonnet-4-20250514"


def test_get_settings_matches_seed_builder_on_all_columns() -> None:
    def column_values(row: AppSettings) -> dict[str, object]:
        return {column.key: getattr(row, column.key) for column in AppSettings.__table__.columns}

    assert column_values(get_settings()) == column_values(build_default_app_settings())
