---
title: "Architecture & Technical Reference"
description: "Seven-module layout, security zones, deployment topologies, API reference."
---

# Septum — Architecture & Technical Reference

<p align="center">
  <a href="../readme.md"><strong>🏠 Home</strong></a>
  &nbsp;·&nbsp;
  <a href="installation.md"><strong>🚀 Installation</strong></a>
  &nbsp;·&nbsp;
  <a href="benchmark.md"><strong>📈 Benchmark</strong></a>
  &nbsp;·&nbsp;
  <a href="features.md"><strong>✨ Features</strong></a>
  &nbsp;·&nbsp;
  <strong>🏗️ Architecture</strong>
  &nbsp;·&nbsp;
  <a href="document-ingestion.md"><strong>📊 Document Ingestion</strong></a>
  &nbsp;·&nbsp;
  <a href="screenshots.md"><strong>📸 Screenshots</strong></a>
</p>

---

## Table of Contents

- [High-Level Architecture](#high-level-architecture)
- [Septum as an AI Privacy Gateway](#septum-as-an-ai-privacy-gateway)
- [Modular Package Layout](#modular-package-layout)
- [Package Internals](#package-internals)
- [Frontend (Next.js App Router) Structure](#frontend-nextjs-app-router-structure)
- [Technology Stack](#technology-stack)
- [Security & Privacy Highlights](#security--privacy-highlights)
- [Audit Trail & Compliance Reporting](#audit-trail--compliance-reporting)
- [LLM Resilience & Observability](#llm-resilience--observability)
- [API Reference](#api-reference)

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

---

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

<p align="center">
  <a href="../readme.md"><strong>🏠 Home</strong></a>
  &nbsp;·&nbsp;
  <a href="installation.md"><strong>🚀 Installation</strong></a>
  &nbsp;·&nbsp;
  <a href="benchmark.md"><strong>📈 Benchmark</strong></a>
  &nbsp;·&nbsp;
  <a href="features.md"><strong>✨ Features</strong></a>
  &nbsp;·&nbsp;
  <strong>🏗️ Architecture</strong>
  &nbsp;·&nbsp;
  <a href="document-ingestion.md"><strong>📊 Document Ingestion</strong></a>
  &nbsp;·&nbsp;
  <a href="screenshots.md"><strong>📸 Screenshots</strong></a>
</p>
