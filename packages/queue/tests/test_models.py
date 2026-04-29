"""Envelope serialization and round-trip tests."""

from __future__ import annotations

import json

from septum_queue import RequestEnvelope, ResponseEnvelope


class TestRequestEnvelope:
    def test_new_assigns_fresh_correlation_id(self):
        a = RequestEnvelope.new(
            provider="anthropic",
            model="claude-3",
            messages=[{"role": "user", "content": "hi"}],
        )
        b = RequestEnvelope.new(
            provider="anthropic",
            model="claude-3",
            messages=[{"role": "user", "content": "hi"}],
        )
        assert a.correlation_id != b.correlation_id
        assert len(a.correlation_id) == 32

    def test_json_round_trip_preserves_fields(self):
        original = RequestEnvelope.new(
            provider="openai",
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "What is [PERSON_1]?"}],
            temperature=0.7,
            max_tokens=512,
            base_url="https://api.example.com",
        )
        data = json.loads(original.to_json())
        restored = RequestEnvelope.from_dict(data)
        assert restored.provider == original.provider
        assert restored.model == original.model
        assert restored.messages == original.messages
        assert restored.temperature == original.temperature
        assert restored.max_tokens == original.max_tokens
        assert restored.base_url == original.base_url
        assert restored.correlation_id == original.correlation_id

    def test_from_dict_applies_defaults_for_optional_fields(self):
        envelope = RequestEnvelope.from_dict(
            {
                "correlation_id": "abc",
                "provider": "anthropic",
                "model": "claude-3",
                "messages": [],
            }
        )
        assert envelope.temperature == 0.2
        assert envelope.max_tokens is None
        assert envelope.base_url is None
        # api_key field was removed; envelope no longer carries secrets.
        assert not hasattr(envelope, "api_key")

    def test_legacy_api_key_field_is_silently_dropped(self):
        """Old payloads still parse but never expose the secret again."""
        envelope = RequestEnvelope.from_dict(
            {
                "correlation_id": "abc",
                "provider": "anthropic",
                "model": "claude-3",
                "messages": [],
                "api_key": "sk-legacy-secret",
            }
        )
        # Round-trip back to a dict — the legacy field must NOT reappear.
        assert "api_key" not in envelope.to_dict()


class TestResponseEnvelope:
    def test_success_round_trip(self):
        original = ResponseEnvelope(
            correlation_id="corr-123",
            text="[PERSON_1] lives at [ADDRESS_1].",
            provider="anthropic",
            model="claude-3",
        )
        restored = ResponseEnvelope.from_dict(json.loads(original.to_json()))
        assert restored.text == original.text
        assert restored.error is None
        assert restored.provider == "anthropic"

    def test_error_round_trip(self):
        original = ResponseEnvelope(
            correlation_id="corr-456",
            error="upstream 502",
            provider="openai",
        )
        restored = ResponseEnvelope.from_dict(json.loads(original.to_json()))
        assert restored.error == "upstream 502"
        assert restored.text is None
