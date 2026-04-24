# septum-core

> 🇹🇷 [Türkçe sürüm](README.tr.md)

Privacy-first PII detection, masking, and unmasking engine. The beating heart of [Septum](https://github.com/byerlikaya/Septum).

## What it does

- **Detect** personally identifiable information (PII) in text using Presidio, spaCy/Transformers NER, and regulation-specific recognizer packs.
- **Mask** detected PII with deterministic placeholders (`[PERSON_1]`, `[EMAIL_2]`, …) while keeping an in-memory anonymization map.
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

## Usage

```python
from septum_core import SeptumEngine

engine = SeptumEngine(regulations=["gdpr", "kvkk"])

result = engine.mask("Ahmet Yılmaz's TC number is 12345678901.")
# result.masked_text → "[PERSON_1]'s TC number is [TCKN_1]."
# result.session_id  → "sess_abc123"
# result.entities    → [{"type": "PERSON", "original": "Ahmet Yılmaz", ...}, ...]

restored = engine.unmask(
    "[PERSON_1] confirmed receipt of the package.",
    session_id=result.session_id,
)
# restored → "Ahmet Yılmaz confirmed receipt of the package."
```

## License

MIT
