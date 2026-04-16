from __future__ import annotations

"""Smoke tests for every built-in regulation recognizer pack.

These tests assert two invariants that must hold for every pack:

1. The pack exposes a ``get_recognizers()`` callable that returns a
   non-empty list of Presidio ``EntityRecognizer`` instances.
2. Loading the pack through Septum's ``PolicyComposer`` and feeding
   its recognizers to ``PIISanitizer`` yields the expected entity
   detections on a representative sample string.

The goal is to guarantee that every regulation listed in
``backend/app/seeds/regulations.py`` is load-ready, so a user can
activate any regulation without silently falling back to the
baseline-only configuration.
"""

import importlib
from dataclasses import dataclass
from typing import Callable, Optional

import pytest
from presidio_analyzer import EntityRecognizer

from septum_api.models.settings import AppSettings
from septum_api.seeds.regulations import builtin_regulations
from septum_api.services.anonymization_map import AnonymizationMap
from septum_api.services.policy_composer import ComposedPolicy
from septum_api.services.sanitizer import PIISanitizer


@dataclass(frozen=True)
class _PackSample:
    """Representative detection case for a regulation pack."""

    text: str
    expected_placeholder: str
    language: str = "en"


_PACK_SAMPLES: dict[str, _PackSample] = {
    "dpdp": _PackSample(
        text="My Aadhaar number is 2341 2341 2346.",
        expected_placeholder="[NATIONAL_ID_",
    ),
    "pdpa_sg": _PackSample(
        text="Singapore NRIC: S1234567D",
        expected_placeholder="[NATIONAL_ID_",
    ),
    "pipl": _PackSample(
        text="Resident ID 11010119900307123X on file.",
        expected_placeholder="[NATIONAL_ID_",
    ),
    "appi": _PackSample(
        text="My Number 1234 5678 9012 issued.",
        expected_placeholder="[NATIONAL_ID_",
    ),
    "popia": _PackSample(
        text="SA ID 8001015009087 recorded.",
        expected_placeholder="[NATIONAL_ID_",
    ),
    "pdpl_sa": _PackSample(
        text="Iqama 2345678901 listed.",
        expected_placeholder="[NATIONAL_ID_",
    ),
    "pdpa_th": _PackSample(
        text="Thai ID 1-2345-67890-12-3 issued.",
        expected_placeholder="[NATIONAL_ID_",
    ),
    "uk_gdpr": _PackSample(
        text="NINO AB123456C registered.",
        expected_placeholder="[NATIONAL_ID_",
    ),
    "pipeda": _PackSample(
        text="Contact: alice@example.ca",
        expected_placeholder="[EMAIL_ADDRESS_",
    ),
    "ccpa": _PackSample(
        text="Reach me at alice@example.com",
        expected_placeholder="[EMAIL_ADDRESS_",
    ),
    "cpra": _PackSample(
        text="Reach me at bob@example.com",
        expected_placeholder="[EMAIL_ADDRESS_",
    ),
    "lgpd": _PackSample(
        text="CNPJ 12.345.678/0001-95 on the invoice.",
        expected_placeholder="[TAX_ID_",
    ),
    "nzpa": _PackSample(
        text="IRD 49-091-850 linked.",
        expected_placeholder="[TAX_ID_",
    ),
    "australia_pa": _PackSample(
        text="TFN 123-456-789 on record.",
        expected_placeholder="[TAX_ID_",
    ),
}


def _all_pack_ids() -> list[str]:
    return [reg.id for reg in builtin_regulations()]


def _load_get_recognizers(reg_id: str) -> Optional[Callable[[], list[EntityRecognizer]]]:
    module_path = f"septum_core.recognizers.{reg_id}.recognizers"
    try:
        module = importlib.import_module(module_path)
    except ModuleNotFoundError:
        return None
    return getattr(module, "get_recognizers", None)


@pytest.fixture
def app_settings() -> AppSettings:
    return AppSettings(
        id=1,
        llm_provider="anthropic",
        llm_model="claude-3-5-sonnet-latest",
        ollama_base_url="http://localhost:11434",
        ollama_chat_model="llama3.2:3b",
        ollama_deanon_model="llama3.2:3b",
        deanon_enabled=True,
        deanon_strategy="simple",
        require_approval=False,
        show_json_output=False,
        use_presidio_layer=True,
        use_ner_layer=False,
        use_ollama_validation_layer=False,
        use_ollama_layer=False,
        chunk_size=800,
        chunk_overlap=200,
        top_k_retrieval=5,
        pdf_chunk_size=1200,
        audio_chunk_size=60,
        spreadsheet_chunk_size=200,
        whisper_model="base",
        image_ocr_languages=["en"],
        ocr_provider="paddleocr",
        ocr_provider_options=None,
        extract_embedded_images=True,
        recursive_email_attachments=True,
        default_active_regulations=["gdpr"],
    )


@pytest.mark.parametrize("reg_id", _all_pack_ids())
def test_every_regulation_has_recognizer_pack(reg_id: str) -> None:
    """Each built-in regulation must ship a loadable recognizer pack."""
    get_recognizers = _load_get_recognizers(reg_id)
    assert get_recognizers is not None, (
        f"Regulation '{reg_id}' has no recognizers.py pack."
    )
    recognizers = list(get_recognizers())
    assert recognizers, (
        f"Regulation '{reg_id}' pack returned no recognizers — "
        "packs must expose at least one EntityRecognizer so users can "
        "activate the regulation without silent fallbacks."
    )
    for rec in recognizers:
        assert isinstance(rec, EntityRecognizer), (
            f"Regulation '{reg_id}' returned a non-EntityRecognizer: "
            f"{type(rec).__name__}"
        )


@pytest.mark.parametrize("reg_id", sorted(_PACK_SAMPLES.keys()))
def test_regulation_pack_detects_sample(
    reg_id: str, app_settings: AppSettings
) -> None:
    """Each pack must detect its representative PII sample end-to-end."""
    get_recognizers = _load_get_recognizers(reg_id)
    assert get_recognizers is not None

    sample = _PACK_SAMPLES[reg_id]
    policy = ComposedPolicy(
        entity_types=[
            "NATIONAL_ID",
            "EMAIL_ADDRESS",
            "TAX_ID",
            "DRIVERS_LICENSE",
            "HEALTH_INSURANCE_ID",
        ],
        recognizers=list(get_recognizers()),
        regulation_ids=[reg_id],
        non_pii_rules=[],
    )

    sanitizer = PIISanitizer(settings=app_settings, policy=policy)
    anon_map = AnonymizationMap(document_id=1, language=sample.language)
    result = sanitizer.sanitize(
        text=sample.text, language=sample.language, anon_map=anon_map
    )

    assert sample.expected_placeholder in result.sanitized_text, (
        f"Pack '{reg_id}' failed to detect sample PII. "
        f"Got: {result.sanitized_text!r}"
    )
