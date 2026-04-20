# Septum — Architecture & Technical Reference

<p align="center">
  <a href="../README.md"><strong>🏠 Home</strong></a>
  &nbsp;·&nbsp;
  <a href="FEATURES.md"><strong>✨ Features</strong></a>
  &nbsp;·&nbsp;
  <strong>🏗️ Architecture</strong>
  &nbsp;·&nbsp;
  <a href="DOCUMENT_INGESTION.md"><strong>📊 Document Ingestion</strong></a>
  &nbsp;·&nbsp;
  <a href="SCREENSHOTS.md"><strong>📸 Screenshots</strong></a>
  &nbsp;·&nbsp;
  <a href="../CONTRIBUTING.md"><strong>🤝 Contributing</strong></a>
  &nbsp;·&nbsp;
  <a href="../CHANGELOG.md"><strong>📝 Changelog</strong></a>
</p>

---

> This document covers Septum's internal architecture, pipeline details, code structure, and deployment options.
> For a high-level overview and quick start, see the [main README](../README.md).

---

## Table of Contents

- [High-Level Architecture](#high-level-architecture)
- [PII Detection & Anonymisation Pipeline](#pii-detection--anonymisation-pipeline)
- [Septum as an AI Privacy Gateway](#septum-as-an-ai-privacy-gateway)
- [Modular Package Layout](#modular-package-layout)
- [Deployment Topologies](#deployment-topologies)
- [Package Internals](#package-internals)
- [Frontend (Next.js App Router) Structure](#frontend-nextjs-app-router-structure)
- [Technology Stack](#technology-stack)
- [Setup](#setup)
- [Running Tests](#running-tests)
- [Security & Privacy Highlights](#security--privacy-highlights)
- [Audit Trail & Compliance Reporting](#audit-trail--compliance-reporting)
- [LLM Resilience & Observability](#llm-resilience--observability)
- [API Reference](#api-reference)
- [Roadmap & Extensibility](#roadmap--extensibility)

---

## High-Level Architecture

Septum is split into **seven independently installable packages** under
`packages/`, grouped into three zones:

- **Air-gapped zone** (`septum-core`, `septum-mcp`, `septum-api`,
  `septum-web`) — handles every PII operation. These packages have zero
  outbound internet dependency; `septum-core` in particular forbids
  `httpx` / `requests` / `urllib` imports so it can never accidentally
  egress raw PII.
- **Bridge** (`septum-queue`) — transports already-masked payloads
  between zones over a file backend (air-gap default) or Redis Streams
  (`[redis]` extra). Raw PII cannot cross the bridge by contract.
- **Internet-facing zone** (`septum-gateway`, `septum-audit`) — forwards
  masked LLM requests to Anthropic / OpenAI / OpenRouter and logs
  PII-free compliance telemetry. By code-review invariant these packages
  never import `septum-core`; the Dockerfile layout enforces this by
  never COPY-ing `packages/core/` into the gateway or audit images.

| Zone | Package | Role |
|:---|:---|:---|
| Air-gapped | `septum-core` | PII detection, masking, unmasking, regulation engine. Zero network deps. |
| Air-gapped | `septum-mcp` | MCP server exposing core tools to Claude Code / Desktop / Cursor over stdio. |
| Air-gapped | `septum-api` | FastAPI REST endpoints, document pipeline, auth, rate limiting. |
| Air-gapped | `septum-web` | Next.js 16 dashboard (App Router + React 19). |
| Bridge | `septum-queue` | Abstract `QueueBackend` Protocol + envelope dataclasses; file / Redis Streams concrete backends. |
| Internet-facing | `septum-gateway` | Cloud LLM forwarder. Consumes masked requests off the queue and publishes masked answers back. |
| Internet-facing | `septum-audit` | Append-only JSONL sink + JSON / CSV / Splunk HEC exporters. Optional queue consumer. |

High-level flow:

1. **Document upload**
   - The frontend sends files via `POST /api/documents/upload`.
   - The backend:
     1. Detects the file type using **python-magic**.
     2. Detects the language (lingua + langdetect).
     3. Routes to the appropriate ingester for the format (PDF, DOCX, XLSX, ODS, image, audio, etc.).
     4. Sends the extracted plain text through the **PolicyComposer + PIISanitizer** pipeline.
     5. Produces **anonymised chunks** and embeds them into FAISS.
     6. Encrypts the original file with AES-256-GCM on disk and stores metadata in SQLite.

2. **Chat flow**
   - The frontend sends messages to `/api/chat/ask` using SSE.
   - The backend:
     1. Sanitises the user query with the same pipeline (active regulations + custom rules).
     2. Retrieves contextual chunks from FAISS.
     3. Uses the **Approval Gate** to show which information will be sent to the cloud.
     4. If the user approves, sends only **placeholder-masked text** to the cloud LLM.
     5. Runs the response through the local **de-anonymiser** so placeholders are mapped back to real values.
     6. Streams the final result to the frontend via SSE.

3. **Settings and regulation management**
   - From the Settings screens you can manage:
     - LLM / Ollama / Whisper / OCR options
     - Default active regulations
     - Custom recognisers
     - NER model mappings

---

## PII Detection & Anonymisation Pipeline

Septum's core is a **multi-layer PII detection pipeline** that combines regulation-aware recognisers, language-specific NER models and country-specific validators under the active policies.

At a high level:

1. **Policy composition**
   - Active regulation rulesets (e.g. GDPR, KVKK, HIPAA, CCPA, LGPD, etc.) are merged into a single **composed policy** via the `PolicyComposer`.
   - The composed policy contains:
     - The union of all entity types that must be protected.
     - A list of recognisers (built-in + custom) that should run for the current configuration.
   - Custom recognisers (regex, keyword list, LLM-prompt based) are also injected into this policy.
   - The 17 built-in pack ids are exposed as a `RegulationId` StrEnum and a `BUILTIN_REGULATION_IDS` frozenset in `septum_core`; downstream packages (`septum-api`, `septum-mcp`) share this canonical registry and a `parse_active_regulations_env` helper for env-driven configuration. The standalone `SeptumEngine` defaults to loading **all 17 packs** (previously the API-side seed was the only source of the full list), and cross-pack duplicate recognisers are deduped at policy build time (~46 → 29 recognisers per mask call).

2. **Layer 1 — Presidio recognisers**
   - Septum uses **Microsoft Presidio** as the first line of detection, with recogniser packs organised by regulation.
   - Each regulation pack contributes recognisers for:
     - Identity (names, national IDs, passports, etc.)
     - Contact details (emails, phones, addresses, IPs, URLs, social handles)
     - Financial identifiers (credit cards, bank accounts, IBAN/SWIFT, tax IDs)
     - Health, demographic and organisational attributes
   - Users can extend this layer with **custom recognisers** (regex patterns, keyword lists, or LLM-prompt based rules).
   - National IDs and financial identifiers use **country-specific checksum validators** to reduce false positives.
   - Only recognisers that are relevant for the active regulations are loaded into the Presidio registry.

3. **Layer 2 — Language-specific NER**
   - For each document and query, Septum detects the language and loads a **language-appropriate HuggingFace NER model**, with a multilingual fallback when needed.
   - The NER layer:
     - Maps only **PERSON_NAME** and **EMAIL_ADDRESS** from model output; ORG and LOC labels are intentionally suppressed to avoid false positives on common words (address/location detection is delegated to Presidio).
     - Uses state-of-the-art XLM-RoBERTa based models (e.g. `Davlan/xlm-roberta-base-wikiann-ner` for 20 languages, `akdeniz27/xlm-roberta-base-turkish-ner` for Turkish).
     - Applies a uniform confidence threshold of **0.85**, a minimum span length of **3 characters**, and snaps all spans to **word boundaries** to prevent mid-word replacements caused by subword tokenisation.
     - NER results are filtered against the active policy's entity types, so only entity types required by active regulations are kept.
     - Skipped entirely for texts shorter than 50 characters to avoid over-sanitisation of short queries.
     - Runs device-aware (CUDA → MPS → CPU) and uses cached pipelines for efficiency.
   - This layer is configurable per language via the **NER Model Settings** screen.
   - An optional **Ollama PII validation layer** (Settings → Privacy) can be enabled to filter false-positive PII candidates at query time (e.g. generic job titles, role names, organisational locations), so that only truly identifying information is masked. Validated national IDs, IBANs, and phone numbers from Presidio **never** go through this LLM step; if the model returns nothing, candidate spans are retained so structured identifiers are not leaked.

4. **Layer 3 — Ollama context-aware layer**
   - When enabled (`use_ollama_layer=True`), Septum uses a **local Ollama LLM** strictly focused on detecting **person names and aliases** that the first two layers may miss:
     - Nicknames, aliases, and informal mentions (e.g. "john" when "John Doe" was detected earlier).
     - Only outputs of type PERSON_NAME, ALIAS, FIRST_NAME, and LAST_NAME are accepted from this layer.
   - This layer preserves exact casing and runs entirely on-device, ensuring no PII leaves the local machine.
   - Skipped for texts shorter than 80 characters. Disabled for numeric-heavy structured content (e.g. price lists, invoices) to avoid noisy detections.

5. **Anonymisation & coreference**
   - All spans from the above layers are merged, deduplicated and fed into the `AnonymizationMap`:
     - Each unique entity is replaced with a stable placeholder (e.g. `[PERSON_1]`, `[EMAIL_2]`).
     - Coreference handling ensures that repeated mentions (e.g. full name → first name) are mapped to the **same** placeholder.
     - The **blocklist** is restricted to person-identifying entity types only (PERSON_NAME, FIRST_NAME, LAST_NAME, ALIAS, USERNAME) to prevent common words from being incorrectly masked as collateral.
   - The anonymisation map never leaves memory and is never written to disk.

6. **Multi-regulation conflict handling**
   - When multiple regulations are active at the same time, Septum always applies the **most restrictive** masking behaviour:
     - If any regulation considers a value PII, it is treated as PII.
     - Overlapping entities are merged into a single placeholder while retaining metadata about which regulations required masking.

In practice, this means Septum does not rely on a single heuristic: it combines regulation packs, NER, custom rules and algorithmic validators into one consistent anonymisation step before anything can leave your environment.

---

## Septum as an AI Privacy Gateway

Beyond the web UI, Septum can act as an **HTTP gateway in front of any LLM-powered application**. Instead of calling a cloud LLM directly, your app can call Septum, which:

1. Sanitises the request (masking PII according to active regulations and custom rules).
2. Retrieves anonymised context from the vector store when RAG is enabled.
3. Forwards only **masked text** to the configured LLM provider.
4. De-anonymises the response locally before returning it to the caller.

Conceptually:

Your app → **Septum (sanitise + RAG + approval)** → Cloud LLM
Your data and raw PII never leave your environment.

A simplified example flow:

1. **Your app** sends a chat request:

   ```json
   POST /api/chat/ask
   {
     "messages": [
       { "role": "user", "content": "Summarise the last 3 contracts for Ahmet Yılmaz at ACME Corp." }
     ],
     "document_ids": [123, 124, 125],
     "metadata": {
       "regulations": ["gdpr", "kvkk"],
       "require_approval": true
     }
   }
   ```

2. **Septum**:
   - Detects language and relevant PII in the query and related documents.
   - Replaces identifiers with placeholders (e.g. `[PERSON_1]`, `[ORG_1]`).
   - Retrieves anonymised chunks from the vector store (RAG).
   - Optionally shows an **approval view** of what will be sent upstream.
   - Calls the configured LLM provider with masked context only.

3. **Cloud LLM** responds with an answer that only contains placeholders.

4. **Septum**:
   - Uses the in-memory anonymisation map to replace placeholders back to original values.
   - Streams the final, human-readable answer back to your app over HTTP/SSE.

In this mode, Septum behaves as a **drop-in privacy layer**:

- Existing tools keep their own UI and logic.
- You centralise PII handling, regulation rules and auditability in one place.
- You can switch or mix LLM providers behind Septum without changing how your app handles personal data.

### Auto-RAG routing

When `document_ids` is omitted from the chat request (or empty), Septum decides automatically whether to search documents or answer as a plain chatbot. A local Ollama intent classifier inspects the query and emits `SEARCH` or `CHAT`. Three paths result:

1. **Manual RAG** — caller supplies `document_ids`. Classifier skipped; retrieval runs against the selected documents as before.
2. **Auto-RAG** — no selection, classifier says `SEARCH`, and the top-k cross-document hybrid search (`_retrieve_chunks_all_documents`) returns chunks whose relevance score is above `rag_relevance_threshold` (default 0.35, configurable from the RAG settings tab). Retrieved chunks go through the approval gate exactly like manual RAG.
3. **Pure LLM** — no selection, classifier says `CHAT`, or no chunks clear the relevance threshold. The LLM answers with no document context attached.

The SSE meta event carries `rag_mode: "manual" | "auto" | "none"` and `matched_document_ids` so the dashboard can display a per-message badge indicating which path was taken. Cross-document retrieval respects user ownership — Auto-RAG only searches documents the caller owns.

---

## Modular Package Layout

Every module lives under `packages/<name>/` and ships with its own
`pyproject.toml`, README, and test suite. Each can be installed and
tested in isolation (`pip install -e "packages/<name>[<extras>]"`).

```
packages/
├── core/                 # septum-core (air-gapped; zero network deps)
│   ├── septum_core/
│   │   ├── detector.py, masker.py, unmasker.py, engine.py
│   │   ├── regulations/
│   │   ├── recognizers/       # 17 regulation packs
│   │   └── national_ids/      # TCKN, SSN, CPF, Aadhaar, IBAN, …
│   ├── tests/
│   └── pyproject.toml          # extras: [transformers], [test]
│
├── mcp/                  # septum-mcp (air-gapped; stdio MCP server)
│   ├── septum_mcp/server.py, tools.py, config.py
│   ├── tests/
│   └── pyproject.toml          # extras: [test]
│
├── api/                  # septum-api (air-gapped; FastAPI)
│   ├── septum_api/
│   │   ├── main.py              # app factory + lifespan + middleware
│   │   ├── bootstrap.py, config.py, database.py
│   │   ├── models/              # SQLAlchemy ORM models
│   │   ├── routers/             # APIRouter modules
│   │   ├── services/            # document pipeline, sanitizer wrappers, …
│   │   ├── middleware/          # auth + rate-limit
│   │   ├── utils/               # crypto, device, text_utils, …
│   │   └── seeds/               # built-in regulation seed data
│   └── pyproject.toml          # extras: [auth], [rate-limit], [postgres], [server], [test]
│
├── web/                  # septum-web (air-gapped; Next.js 16 dashboard)
│   ├── src/app/, src/components/, src/store/, src/i18n/
│   └── package.json
│
├── queue/                # septum-queue (bridge)
│   ├── septum_queue/
│   │   ├── base.py               # QueueBackend Protocol + QueueSession
│   │   ├── models.py             # RequestEnvelope / ResponseEnvelope
│   │   ├── file_backend.py       # POSIX atomic-rename, air-gap default
│   │   └── redis_backend.py      # Redis Streams consumer groups
│   └── pyproject.toml          # extras: [redis], [test]
│
├── gateway/              # septum-gateway (internet-facing)
│   ├── septum_gateway/
│   │   ├── config.py             # GatewayConfig, env resolution
│   │   ├── forwarder.py          # Anthropic / OpenAI / OpenRouter clients
│   │   ├── response_handler.py   # GatewayConsumer + optional audit hook
│   │   ├── worker.py             # python -m septum_gateway entry point
│   │   └── main.py               # FastAPI /health (optional [server] extra)
│   └── pyproject.toml          # extras: [server], [test]  — NEVER depends on septum-core
│
└── audit/                # septum-audit (internet-facing)
    ├── septum_audit/
    │   ├── events.py             # AuditRecord envelope
    │   ├── sink.py               # JsonlFileSink + MemorySink
    │   ├── exporters/            # JSON / CSV / Splunk HEC
    │   ├── retention.py          # Age + count cap, atomic rewrite
    │   ├── consumer.py           # AuditConsumer (queue → sink)
    │   ├── worker.py             # python -m septum_audit entry point
    │   └── main.py               # FastAPI /health + /api/audit/export
    └── pyproject.toml          # extras: [queue], [server], [test]  — NEVER depends on septum-core
```

**Dependency graph** (`→` means "depends on"):

```
septum-core ← septum-mcp
septum-core ← septum-api ← septum-web (HTTP, runtime only)
septum-queue ← septum-api (producer, via septum_api.services.gateway_client)
septum-queue ← septum-gateway (consumer + response producer)
septum-queue ← septum-audit[queue] (event consumer)
```

`septum-core` is the only package with zero Python runtime dependencies
inside the Septum graph. `septum-queue` ships with zero required deps
too (stdlib only); the Redis backend lives behind the `[redis]` extra.

The legacy `backend/` compatibility layer has been removed. Every
module lives under `packages/` now, imports go directly through
`septum_api.*`, and the top-level `Dockerfile` + compose variants
point at `packages/api/` for the backend code path.

With FastAPI we follow Context7 best practices:

- API endpoints are modularised with **APIRouter**.
- Request/response validation uses Pydantic models.
- DB session, settings and other dependencies are injected via `Depends(...)`.
- All path functions are async; CPU-bound tasks run in a thread pool.

---

## Deployment Topologies

Four Docker Compose variants cover every deployment shape; all four are
validated by `docker-compose config` and build cleanly on
Docker 29+ (linux/amd64 + linux/arm64).

| Topology | File | Contains | When to use |
|:---|:---|:---|:---|
| Standalone | `docker-compose.standalone.yml` | One container from `docker/standalone.Dockerfile`, SQLite | Simplest install; published as `byerlikaya/septum:latest`. |
| Full dev stack | `docker-compose.yml` | api + web + gateway-worker + audit-worker + audit-api + Postgres + Redis + optional Ollama profile | Local development or single-host install with zone logic in place. |
| Air-gapped zone | `docker-compose.airgap.yml` | api + web + Postgres + Redis (no gateway). `USE_GATEWAY_DEFAULT=true` routes cloud calls over Redis Streams. | Internal host in a two-host split. |
| Internet-facing zone | `docker-compose.gateway.yml` | gateway-worker + gateway-health + audit-worker + audit-api + Redis. Uses YAML anchors (`x-gateway-base`, `x-audit-base`) to dedupe service definitions. | DMZ / cloud host in a two-host split. |

For a true air-gapped deployment, run `airgap.yml` on the internal host
and `gateway.yml` on the DMZ host and point both at the same Redis over
a VPN / private link. The queue carries only masked text; raw PII never
crosses the bridge.

**Per-module Dockerfiles** live under `docker/` — `api.Dockerfile`,
`web.Dockerfile`, `gateway.Dockerfile`, `audit.Dockerfile`,
`mcp.Dockerfile`, `standalone.Dockerfile`. The gateway and audit images
are lightweight (~250 MB each, no torch / Presidio / spaCy) and — by
image-layer contract — never COPY `packages/core/` into their runtime
stage. The api and standalone images ship the full ML stack (CPU-only
torch, Presidio, spaCy, PaddleOCR, Whisper, FAISS, BM25) at ~9.8 GB and
~5.7 GB respectively.

Every HTTP service ships a Docker `HEALTHCHECK` using
`python -c "import urllib.request; urllib.request.urlopen('http://.../health')"`
(the `web` image uses `wget` since it runs on `node:20-alpine`). The
MCP image defaults to streamable-http on port 8765 and ships the same
`/health` healthcheck; for stdio deployments the container is still
launchable via `SEPTUM_MCP_TRANSPORT=stdio` and the orchestrator then
treats the subprocess exit code as liveness.

---

## Package Internals

### septum-core

The PII engine: detection, masking, unmasking, regulation composition.
Zero network dependencies by contract — no `httpx` / `requests` /
`urllib` imports anywhere under `septum_core/`.

- `detector.py` — `Detector` class wraps the multi-layer pipeline
  (Presidio → Transformers NER → optional semantic validation via a
  `SemanticDetectionPort` adapter).
- `masker.py`, `unmasker.py` — placeholder creation and restoration.
- `anonymization_map.py` — session-scoped `PII ↔ placeholder` map with
  coreference resolution.
- `engine.py` — `SeptumEngine` facade: `engine.mask(text)` /
  `engine.unmask(text, session_id)`. Includes an in-memory session
  registry with TTL eviction for long-running MCP subprocesses.
- `regulations/` — `PolicyComposer` merges active regulation rulesets;
  cross-pack duplicate recognisers are deduped at build time
  (~46 → 29 recognisers per mask call).
- `recognizers/` — 17 regulation packs (GDPR, KVKK, HIPAA, CCPA, LGPD,
  PIPEDA, PDPA_TH, PDPA_SG, APPI, PIPL, POPIA, DPDP, UK_GDPR, PDPL_SA,
  NZPA, Australia_PA, CPRA) with `base_recognizer` + `RecognizerRegistry`.
  Each pack declares its own `ENTITY_TYPES` constant so the standalone
  engine sees the same entity list the API seed uses.
  `recognizers/__init__.py` exports the canonical `RegulationId`
  StrEnum + `BUILTIN_REGULATION_IDS` tuple + a
  `parse_active_regulations_env(value)` helper that replaces three
  duplicated env-parsing blocks across the api / mcp / standalone
  entry points.
- `national_ids/` — country-specific ID validators with algorithmic
  checksums (TCKN, SSN, CPF, Aadhaar, IBAN, …).

### septum-mcp

MCP server exposing six tools (`mask_text`, `unmask_response`,
`detect_pii`, `scan_file`, `list_regulations`, `get_session_map`)
over any of the three standard MCP transports:

- **stdio** (default) — for subprocess-launching clients (Claude Code,
  Claude Desktop, Cursor, Windsurf, Zed).
- **streamable-http** — modern HTTP transport for remote, containerised,
  and browser clients. Gated by a static bearer token via the
  ``septum_mcp.auth.BearerTokenMiddleware`` ASGI middleware (uses
  ``hmac.compare_digest`` for constant-time comparison).
- **sse** — legacy HTTP + Server-Sent Events, kept for clients that
  haven't migrated to streamable-http yet.

Transport is selected via ``--transport`` CLI flag or the
``SEPTUM_MCP_TRANSPORT`` env var. HTTP mode also supports
``--host``/``--port``/``--token``/``--mount-path`` flags and their
``SEPTUM_MCP_HTTP_*`` env var equivalents. The ``/health`` endpoint
answers 200 OK unconditionally and bypasses the bearer check so
Docker ``HEALTHCHECK`` and reverse-proxy probes work without a token.

Depends on `septum-core`; engine construction is deferred to the
first tool call so idle cost is near zero. When HTTP mode is active
``uvicorn`` is started as the ASGI server; stdio callers never touch
the HTTP stack. Single-tenant today — all HTTP clients share one
``SeptumEngine`` and therefore one anonymization-session registry.

### septum-api

The FastAPI REST layer under `packages/api/septum_api/`:

- `main.py` — app factory + lifespan + middleware stack (CORS, auth,
  rate limit, Prometheus).
- `bootstrap.py`, `config.py`, `database.py` — infrastructure config
  (`config.json`), lazy async engine, settings.
- `models/` — SQLAlchemy ORM models (`AppSettings`, `Document`,
  `Chunk`, `User`, `ApiKey`, `AuditEvent`, …).
- `routers/` — 14 APIRouter modules (auth, api_keys, chat,
  chat_sessions, chunks, documents, regulations, settings, setup,
  users, approval, audit, error_logs, text_normalization).
- `services/` — business logic: `document_pipeline`, `sanitizer`
  (wrapper around core), `llm_router`, `vector_store`,
  `bm25_retriever`, `deanonymizer`, `prompts`, `approval_gate`,
  `gateway_client` (queue producer), `ingestion/` (format-specific
  extractors), `llm_providers/`, `recognizers/` (adapter layer over
  core), `national_ids/` (adapter layer over core).
- `middleware/` — `auth.py` (JWT + API key), `rate_limit.py`.
- `utils/` — `crypto.py` (AES-256-GCM), `auth_dependency.py`,
  `device.py`, `logging_config.py`, `metrics.py`, `text_utils.py`.

### septum-queue

Abstract queue transport, zero runtime deps:

- `base.py` — `QueueBackend` Protocol + `QueueSession` context manager
  + `QueueError` / `QueueTimeoutError`.
- `models.py` — `Message`, `RequestEnvelope`, `ResponseEnvelope`
  dataclasses; `to_dict()` / `to_json()` / `from_dict()` on each.
- `file_backend.py` — `FileQueueBackend`: atomic `os.replace` is the
  entire synchronization primitive. Three directories per topic
  (`incoming/`, `processing/`, `done/`); claiming a message is a rename
  that only one consumer can win.
- `redis_backend.py` — `RedisStreamsQueueBackend`: XADD / XREADGROUP /
  XACK with consumer groups for at-least-once semantics.
- `backend_from_env(topic)` — env-var dispatch (`SEPTUM_QUEUE_URL` →
  Redis, `SEPTUM_QUEUE_DIR` → file). Missing both raises `SystemExit`
  rather than silently defaulting.

### septum-gateway

Cloud LLM forwarder for the internet-facing zone:

- `config.py` — `GatewayConfig.from_env()` reads `SEPTUM_GATEWAY_*` env
  vars plus legacy `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` /
  `OPENROUTER_API_KEY`.
- `forwarder.py` — `BaseForwarder` + `AnthropicForwarder` +
  `_OpenAICompatibleForwarder` (`OpenAIForwarder`,
  `OpenRouterForwarder`) + `ForwarderRegistry.from_config()` +
  `_post_with_retries` with exponential backoff.
- `response_handler.py` — `GatewayConsumer.run_forever()` pairs request
  envelopes with responses; optional `audit_queue` emits a PII-free
  telemetry envelope per handled request (provider / model /
  status / latency_ms / correlation_id — no prompts, no response text,
  no api keys).
- `worker.py` + `__main__.py` — `python -m septum_gateway` entry point.
- `main.py` — FastAPI `/health` endpoint behind the `[server]` extra.

### septum-audit

Compliance audit trail:

- `events.py` — `AuditRecord` envelope.
- `sink.py` — `AuditSink` Protocol + `JsonlFileSink` (append-only,
  logrotate-safe, POSIX atomic-append) + `MemorySink`.
- `exporters/` — `JsonExporter`, `CsvExporter`, `SplunkHecExporter` all
  sharing a `BaseExporter(iter_chunks)` streaming primitive.
- `retention.py` — `RetentionPolicy(max_age_days, max_records)` +
  atomic in-place JSONL rewrite via `.tmp` + `os.replace`.
- `consumer.py` — `AuditConsumer(queue, sink)`.
- `worker.py` + `__main__.py` — `python -m septum_audit` entry point.
- `main.py` — FastAPI `/health` + streaming `/api/audit/export`.

---

## Frontend (Next.js App Router) Structure

Frontend root: `packages/web/`

- `src/app/`
  - `layout.tsx` — root layout
  - `page.tsx` — landing / redirect
  - `chat/page.tsx` — chat screen (connected to backend via SSE)
  - `documents/page.tsx` — document list and upload
  - `settings/` — sub-pages:
    - `page.tsx` — general settings
    - `regulations/page.tsx` — regulation management
    - `custom-rules/page.tsx` — custom recogniser builder
- `src/components/`
  - `layout/Sidebar.tsx`, `layout/Header.tsx`
  - `chat/ChatWindow.tsx`, `MessageBubble.tsx`, `ApprovalModal.tsx`, `JsonOutputPanel.tsx`, `DeanonymizationBanner.tsx`
  - `documents/DocumentUploader.tsx`, `DocumentList.tsx`, `DocumentCard.tsx`, `DocumentPreview.tsx`, `TranscriptionPreview.tsx`
  - `settings/*` — `LLMSettings`, `PrivacySettings`, `LocalModelSettings`, `RAGSettings`, `IngestionSettings`, `NERModelSettings`, `RegulationManager`, `CustomRuleBuilder`
- `src/store/`
  - `chatStore.ts`, `documentStore.ts`, `settingsStore.ts`, `regulationStore.ts`
- `src/lib/`
  - `api.ts` — backend HTTP client
  - `types.ts` — shared types

On the Next.js side we follow Context7 best practices:

- Uses the App Router (segment-based routing).
- SSE and streaming responses use `EventSource` or `fetch` + `ReadableStream`.
- Tailwind CSS is configured to scan `app`, `components` and related directories.

---

## Technology Stack

**Backend**
- Python, FastAPI, Uvicorn
- Presidio Analyzer/Anonymizer
- HuggingFace Transformers + sentence-transformers
- faiss-cpu
- lingua-language-detector, langdetect
- PaddleOCR, OpenCV, Pillow
- Whisper, ffmpeg-python
- SQLAlchemy + asyncpg (PostgreSQL) / aiosqlite (SQLite)
- Alembic (schema migrations)
- Redis (optional anonymization map caching)
- cryptography (AES-256-GCM)

**Frontend**
- Next.js 16 (App Router)
- React 19
- TypeScript
- Tailwind CSS
- axios, react-dropzone, lucide-react

**Infrastructure**
- Docker Compose (PostgreSQL 16 + Redis 7 + api + web + gateway + audit); 4 topology variants under `docker-compose*.yml`
- Ollama (optional local LLM fallback)

---

## Setup

### Option A: Docker Compose (recommended)

The fastest way to get Septum running with all dependencies (PostgreSQL, Redis):

```bash
docker compose up
```

The setup wizard handles all configuration on first visit. This starts:
- **PostgreSQL 16** — production database
- **Redis 7** — anonymization map caching for multi-worker support
- **Septum** (ports 8000 + 3000) — Backend + Frontend in a single container

To include a local Ollama instance:

```bash
docker compose --profile ollama up
```

### Option B: Local development

#### 1. Shared prerequisites

- Python 3.11+ (tested up to 3.13)
- Node.js 20+ (for Next.js 16)
- ffmpeg (for Whisper)

#### 2. One-shot setup

```bash
./dev.sh --setup     # installs all modular packages (editable) + packages/api/requirements.txt + npm
```

`dev.sh --setup` installs every `packages/*` module in editable mode
with its development extras (`septum-core[transformers,test]`,
`septum-queue[redis,test]`, `septum-api[auth,rate-limit,postgres,server,test]`,
`septum-mcp[test]`, `septum-gateway[server,test]`,
`septum-audit[queue,server,test]`), then pulls the heavy ML / OCR /
Whisper / ingestion deps from `packages/api/requirements.txt`.

#### 3. Start the dev stack

```bash
./dev.sh             # starts api (septum_api.main:app on port 8000) + web (packages/web, port 3000)
```

By default, everything is served on a single port:
- UI + API: `http://localhost:3000`
- API docs: `http://localhost:3000/docs`

Next.js rewrites proxy `/api/*`, `/docs`, `/health`, and `/metrics` to the internal backend (port 8000 inside the container). Port 8000 is not exposed externally.

The API base URL in `packages/web/src/lib/api.ts` is driven by the
build-time `NEXT_PUBLIC_API_BASE_URL` env var. Unset means same-origin
proxy via Next.js rewrites (the default). For a split deployment
(separate hosts for api and web) set this at build time to the api's
public URL.

All configuration is handled by the setup wizard on first run. A
`config.json` file is auto-generated (default location:
top-level `config.json`; override with `SEPTUM_CONFIG_PATH`) with
encryption keys and infrastructure settings. No manual configuration
files needed.

**First launch:** The web UI runs a short setup wizard (LLM provider and connection test) until onboarding is marked complete in application settings. Chat conversations are persisted server-side (`/api/chat-sessions`) and can be switched from the chat sidebar.

#### 4. Reset local state

```bash
./dev.sh --reset     # wipes DB, config.json, uploads, indexes, anon_maps (top-level runtime state)
```

---

## Running Tests

Tests live in two places:

- **Modular package tests** under `packages/<name>/tests/` — isolated,
  fast, and install-independent (each `pytest packages/<name>/tests/`
  works without the rest of the repo).
- **API integration tests** under `packages/api/tests/` — exercise the
  full document + chat pipeline end-to-end. These tests also cover
  bootstrap, database, routers, services, utils, and auth middleware.

```bash
# Everything (shell glob expansion required — pytest packages/ alone
# trips on the shared 'tests' namespace across packages)
pytest packages/*/tests -q

# Single modular package
pytest packages/core/tests/ -q
pytest packages/queue/tests/ -q
pytest packages/gateway/tests/ -q
pytest packages/audit/tests/ -q
pytest packages/mcp/tests/ -q
pytest packages/api/tests/ -q
```

The `/test` skill inside Claude Code picks the right test file based on
the changed source. Examples:
- `packages/api/septum_api/services/sanitizer.py` → `packages/api/tests/test_sanitizer.py`
- `packages/queue/septum_queue/file_backend.py` → `packages/queue/tests/test_file_backend.py`
- `packages/audit/septum_audit/retention.py` → `packages/audit/tests/test_retention.py`

**Continuous integration:** `.github/workflows/tests.yml` runs a
parallel matrix — `backend-tests` (pip install every package editable +
`packages/api/requirements.txt` + pytest `packages/api/tests`), `modular-tests`
(each package installed and tested in its own step), plus backend lint
(Ruff + Bandit), backend security (`pip-audit`), frontend Jest, frontend
typecheck (`tsc --noEmit`), frontend `npm audit`.

Any tests that would send real requests to a cloud LLM **must be
mocked**; tests that hit real external LLM APIs are treated as bugs.

---

## Security & Privacy Highlights

- Raw PII is never logged and never sent to the cloud.
- The anonymisation map (placeholders → real values) is cached in memory, optionally in Redis, and persisted encrypted on disk with AES-256-GCM. It is never sent to the frontend, cloud, or logs.
- File types are detected by content signature, not by extension.
- Uploaded files are stored encrypted on disk with AES-256-GCM; decryption happens only in memory during preview.
- When multiple regulations are active at the same time, Septum always applies the **most restrictive** masking policy.
- Optional **JWT authentication** (`POST /api/auth/register`, `POST /api/auth/login`, `GET /api/auth/me`): each user has a **role** (`admin`, `editor`, or `viewer`); documents and chat sessions are scoped to the signed-in user when a token is present; sensitive settings updates require `admin`.

---

## Audit Trail & Compliance Reporting

Septum maintains an **append-only audit trail** for GDPR, KVKK, and other regulation compliance:

- **Events tracked:** PII detection (per entity type and count), placeholder geri yazma (de-anonymisation), document upload/delete, regulation changes.
- **No raw PII stored:** Audit events record entity type names and counts only — never original values.
- **Entity provenance link:** each `EntityDetection` row carries an `audit_event_id` FK back to the `AuditEvent` that produced it, so the dashboard can jump from a log entry to the exact entities it covered.
- **REST API:**
  - `GET /api/audit` — paginated, filterable by event type, document, session, date range, and **entity type** (matches events whose linked detections include the given type via an `EXISTS` correlated subquery on `entity_detections.audit_event_id`).
  - `GET /api/audit/{event_id}/entity-detections` — returns the `EntityDetection` rows linked to a specific event (empty for pre-linking events).
  - `GET /api/audit/{document_id}/report` — compliance report for a specific document.
  - `GET /api/audit/session/{session_id}` — full audit trail for a chat session.
  - `GET /api/audit/metrics` — aggregate PII detection quality metrics (entity type distribution, coverage ratios, per-document averages).
- **Frontend:** Audit log viewer in Settings → Audit Trail with event type badges, entity type filter dropdown, entity breakdowns, pagination, and a **"Focus on these entities"** button on each `pii_detected` card that opens the document preview highlighting only that event's detections.

---

## LLM Resilience & Observability

- **Circuit breaker:** After 3 consecutive cloud LLM failures within 120 seconds, the provider is temporarily disabled (60-second cooldown). Requests skip directly to the local Ollama fallback without wasting retry time. After cooldown, a single probe request tests recovery.
- **Multi-provider support:** Anthropic, OpenAI, OpenRouter, and local Ollama. Switch providers via Settings UI without code changes.
- **Retry with exponential backoff:** Cloud HTTP calls retry up to 3 times with exponential backoff (0.5s → 1s → 2s).
- **Health endpoint:** `GET /health` reports backend status, device info, LLM provider, Redis connectivity, and per-provider circuit breaker state.

---

## API Reference

Septum exposes a RESTful API. Key endpoint groups:

| Group | Endpoints | Description |
|-------|-----------|-------------|
| **Documents** | `POST /api/documents`, `GET /api/documents`, `GET /api/documents/{id}`, `GET /api/documents/{id}/raw`, `GET /api/documents/{id}/anon-summary`, `DELETE /api/documents/{id}`, `POST /api/documents/{id}/reprocess` | Upload, list, preview/decrypt original, anonymisation summary, delete, reprocess |
| **Auth** | `POST /api/auth/register`, `POST /api/auth/login`, `GET /api/auth/me` | JWT bearer accounts (roles: admin, editor, viewer) |
| **Chat** | `POST /api/chat/ask` (SSE), `GET /api/chat/debug/{session_id}` | Privacy-preserving RAG chat with streaming |
| **Chat sessions** | `GET/POST /api/chat-sessions`, `GET/PATCH/DELETE /api/chat-sessions/{id}`, `POST /api/chat-sessions/{id}/messages` | Persistent chat history (list sessions, update metadata, append messages) |
| **Chunks** | `GET /api/chunks`, `GET /api/chunks/{id}` | Search and inspect document chunks |
| **Settings** | `GET /api/settings`, `PUT /api/settings` | Application configuration |
| **Regulations** | `GET /api/regulations`, `PUT /api/regulations/{id}` | Manage regulation rulesets and custom recognisers |
| **Audit** | `GET /api/audit`, `GET /api/audit/{event_id}/entity-detections`, `GET /api/audit/{document_id}/report`, `GET /api/audit/session/{session_id}`, `GET /api/audit/metrics` | Compliance audit trail, per-event entity provenance, and detection metrics |
| **Health** | `GET /health`, `GET /metrics` | System health and Prometheus metrics |

Full OpenAPI schema is available at `http://localhost:3000/docs` when the application is running.

---

## Roadmap & Extensibility

- Add new country regulations by creating new regulation packs in the recogniser registry.
- Add new national ID formats by adding validators and recognisers in the national ID module.
- Add new document formats by implementing dedicated ingesters in the ingestion layer.
- Update NER model mappings from the Settings → NER Models screen.
- Pronoun coreference resolution via local LLM (Ollama) detects implied subject references.
- PII detection quality metrics for data-driven assessment of detection coverage.
