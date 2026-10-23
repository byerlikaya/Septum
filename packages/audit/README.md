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

## Retention

Two caps prune the live JSONL ledger only: `SEPTUM_AUDIT_RETENTION_DAYS` (age) and `SEPTUM_AUDIT_RETENTION_MAX_RECORDS` (count). An unset cap keeps everything. Pruning runs at startup and then once per effective interval: the configured `SEPTUM_AUDIT_RETENTION_INTERVAL_SECONDS` (default `3600`), or longer when a pass is slow. `/health` returns 200 for `ok`, `disabled` and `not_reported`, and 503 for `halted`, `failed`, `blocked`, `stale`, `missing` and `unreadable`. The consumer adds `consumer`: `ok` or `not_reported` (200), or `consumer_failing` or `consumer_unreadable` (503). `halted` means restore the ledger and its anchor from a verified copy. `/health` no longer returns `sink_path`. Full reference: the installation guide, section "Audit event retention".

- Nothing is dropped for its content. `attributes` is normalised before hashing: keys become strings, a list of pairs becomes a dict, other non-mapping values become `{"value": ...}`, top-level key collisions are kept as a list under `"value"`, and an unserialisable value becomes `"<unserialisable>"`. Non-finite numbers become strings. A zero, boolean, string, missing, out-of-range or non-finite `timestamp` is replaced with the ingest time, and the original is not kept. Each of these sets the record-level `ingest_replaced` flag (it says a value was replaced, not which field). A record the sink cannot store as received is kept as a placeholder with its id (cut to 64 characters), source, event type and correlation id, and no content. The consumer acknowledges and logs a payload that is not an object as malformed, and writes nothing. Any other failure requeues the message, and retries back off from 2 seconds, doubling each time, capped at 60 seconds; the failure is logged on the first attempt and then every 10th. A record the sink refuses is kept as a placeholder, acknowledged only after it is written; if that write fails, the message is requeued. Objects nested deeper than 32 levels are replaced by `"<depth limit>"`. The sink drops any producer copy of `ingest_replaced` and `ingest_unstorable`, so a producer cannot forge either marker. Exports render these as strings, so their output stays strict JSON. Lines whose non-finite values were rendered as strings do not re-verify with `verify_chain`.

## License

MIT — see [LICENSE](../../LICENSE).
