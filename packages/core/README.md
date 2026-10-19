# septum-core

> 🇹🇷 [Türkçe sürüm](README.tr.md)

Privacy-first PII detection, masking, and unmasking engine. The beating heart of [Septum](https://github.com/byerlikaya/Septum).

## What it does

- **Detect** personally identifiable information (PII) in text using Presidio, spaCy/Transformers NER, and regulation-specific recognizer packs.
- **Mask** detected PII with deterministic placeholders (`[PERSON_NAME_1]`, `[EMAIL_ADDRESS_2]`, …) while keeping an in-memory anonymization map.
- **Unmask** LLM responses by restoring original values from that map.
- **Compose** multiple privacy regulations (GDPR, KVKK, HIPAA, CCPA, LGPD, …) into a single detection pipeline, applying the most restrictive rule.

## Design guarantees

- **Zero network dependencies.** `septum-core` never imports `httpx`, `requests`, `urllib`, `aiohttp`, or any other HTTP client. It is safe to run on air-gapped hardware.
- **No database coupling.** The engine accepts pre-loaded regulation data through plain dataclasses. DB access lives in `septum-api`, not here.
- **Pluggable semantic layer.** Optional LLM-assisted PII detection (e.g. via Ollama) is injected through the `SemanticDetectionPort` protocol — never imported directly.

## Install

```bash
pip install -e packages/core
```

For the HuggingFace NER layer:

```bash
pip install -e "packages/core[transformers]"
```

## Model pinning

The default NER, embedding and OCR models are pinned to Hugging Face commits, and every file is checked against its SHA256 before it loads. Offline runs use `HF_HUB_OFFLINE=1`, which refuses anything not already cached and verified. The fetch CLI (`python -m septum_api.cli.models fetch`) lives in `septum-api`; see [architecture](../../docs/architecture.md#model-integrity).

## Usage

```python
from septum_core import SeptumEngine

engine = SeptumEngine(regulations=["gdpr", "kvkk"])

result = engine.mask("Elif Demir's TCKN is 10000000146.")
# result.masked_text       → "[PERSON_NAME_1]'s TCKN is [NATIONAL_ID_1]."
# result.session_id        → "sess_abc123"
# result.entity_type_counts → {"PERSON_NAME": 1, "NATIONAL_ID": 1}

restored = engine.unmask(
    "[PERSON_NAME_1] confirmed the follow-up appointment.",
    session_id=result.session_id,
)
# restored → "Elif Demir confirmed the follow-up appointment."
```

## License

MIT
