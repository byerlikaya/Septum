# septum-audit

> 🇹🇷 [Türkçe sürüm](README.tr.md)

Internet-facing compliance audit trail for Septum. Persists already-masked
event records and exports them to JSON, CSV, or Splunk HEC for downstream
SIEM pipelines.

`septum-audit` runs in the **internet-facing zone** alongside
`septum-gateway`. By design it never imports `septum-core`: every record
it observes has already been scrubbed of raw PII by the air-gapped
producer. The dependency wall is what makes that invariant enforceable
in code review.

## Install

```bash
pip install septum-audit                  # base: stdlib-only sinks + exporters
pip install "septum-audit[queue]"         # adds septum-queue consumer
pip install "septum-audit[server]"        # adds FastAPI export endpoint
```

## Quick start

```python
from septum_audit import (
    AuditRecord,
    JsonlFileSink,
    JsonExporter,
    RetentionPolicy,
    apply_retention_to_jsonl,
)

sink = JsonlFileSink("/var/log/septum/audit.jsonl")
await sink.write(AuditRecord(
    source="septum-api",
    event_type="pii.detected",
    correlation_id="req-123",
    attributes={"entity_count": 4, "regulation_ids": ["gdpr", "kvkk"]},
))

# Stream to Splunk HEC
import sys
JsonExporter().write(sink.read_all(), sys.stdout)

# Trim records older than 90 days, cap at 1M rows
apply_retention_to_jsonl(
    "/var/log/septum/audit.jsonl",
    RetentionPolicy(max_age_days=90, max_records=1_000_000),
)
```

## Architecture

| Component | File | Description |
|---|---|---|
| `AuditRecord` | `events.py` | Immutable envelope; `source`, `event_type`, `correlation_id`, `attributes` |
| `AuditSink` (Protocol) | `sink.py` | Write contract every backend implements |
| `JsonlFileSink` | `sink.py` | Append-only newline-delimited JSON; logrotate-friendly |
| `MemorySink` | `sink.py` | In-process list; for tests + ephemeral counts |
| `JsonExporter` / `CsvExporter` / `SplunkHecExporter` | `exporters/` | Format-specific byte writers |
| `RetentionPolicy` | `retention.py` | Age + count cap; atomic in-place rewrite |
| `AuditConsumer` | `consumer.py` *(queue extra)* | Reads `septum-queue` topic into a sink |
| `create_app()` | `main.py` *(server extra)* | FastAPI with `/health` and `/api/audit/export` |

## Zone discipline

```
┌─────────────────────────┐    queue    ┌──────────────────────────┐
│  Air-gapped zone        │   topic     │  Internet-facing zone    │
│  septum-api / -core     ├────────────►│  septum-gateway          │
│                         │ (masked)    │  septum-audit            │
└─────────────────────────┘             └──────────────────────────┘
```

`septum-audit` only ever sees what `septum-gateway` already accepted: a
small per-request envelope with provider / model / correlation id /
status / latency. No prompts, no responses, no document content.

## License

MIT — see [LICENSE](../../LICENSE).
