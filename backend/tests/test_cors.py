"""Unit tests for the FRONTEND_ORIGIN → CORS allow-list parser."""

from __future__ import annotations

import pytest

from septum_api.main import _resolve_cors_origins


class TestResolveCorsOrigins:
    """``_resolve_cors_origins`` parses ``frontend_origin`` config."""

    @pytest.mark.parametrize("value", ["", "*", "  *  "])
    def test_wildcard_or_empty_returns_wildcard(self, value: str):
        assert _resolve_cors_origins(value) == ["*"]

    def test_single_origin(self):
        assert _resolve_cors_origins("https://app.example.com") == [
            "https://app.example.com"
        ]

    def test_comma_separated_origins_are_split_and_trimmed(self):
        value = "https://app.example.com, https://admin.example.com"
        assert _resolve_cors_origins(value) == [
            "https://app.example.com",
            "https://admin.example.com",
        ]

    def test_blank_segments_are_dropped(self):
        # Trailing comma or stray whitespace should not produce empty
        # entries that the CORS middleware would treat as origins.
        assert _resolve_cors_origins("https://a.example, ,") == [
            "https://a.example",
        ]
