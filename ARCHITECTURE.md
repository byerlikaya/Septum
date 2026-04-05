# Septum — Architecture & Technical Reference

> This document covers Septum's internal architecture, pipeline details, code structure, and deployment options.
> For a high-level overview and quick start, see [README.md](README.md).

---

## Table of Contents

- [High-Level Architecture](#high-level-architecture)
- [PII Detection & Anonymisation Pipeline](#pii-detection--anonymisation-pipeline)
- [Septum as an AI Privacy Gateway](#septum-as-an-ai-privacy-gateway)
- [Backend (FastAPI) Structure](#backend-fastapi-structure)
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

- **Backend**: Python + FastAPI — handles document processing, anonymisation, encryption and LLM integration. All PII handling happens on the server side you control.
- **Frontend**: Next.js 16 + React 19 — provides chat, document management, settings and regulation views. Communicates with the backend over HTTP and SSE streams.

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

---

## Backend (FastAPI) Structure

Backend root: `backend/`

- `app/main.py` — FastAPI application definition and router registration
- `app/config.py` — configuration via Pydantic Settings
- `app/database.py` — SQLite connection and `RegulationRuleset` seeding
- `app/models/` — SQLAlchemy models:
  - `document.py`, `chunk.py`, `settings.py`, `regulation.py`, `custom_recognizer.py`
- `app/schemas/` — Pydantic schemas:
  - `document.py`, `chat.py`, `settings.py`, `regulation.py`, `custom_recognizer.py`
- `app/routers/` — FastAPI routers:
  - `documents.py`, `chunks.py`, `chat.py`, `approval.py`, `settings.py`, `regulations.py`, `error_logs.py`, `text_normalization.py`
- `app/services/`:
  - `ingestion/` — format-specific ingesters (PDF, DOCX, XLSX, ODS, image/OCR, audio/Whisper)
  - `recognizers/` — regulation packs (gdpr, hipaa, kvkk, lgpd, ccpa, …) and `registry.py`
  - `national_ids/` — country-specific ID validators (TCKN, SSN, CPF, Aadhaar, IBAN, etc.)
  - `policy_composer.py` — composes active regulations and custom rules into a single policy
  - `ner_model_registry.py` — language → model mapping and lazy loading
  - `sanitizer.py` — PII detection and placeholder pipeline
  - `anonymization_map.py` — session-scoped anonymisation map + coreference handling
  - `document_pipeline.py`, `vector_store.py`, `llm_router.py`, `deanonymizer.py`, `approval_gate.py`
  - `prompts.py` — centralized LLM prompt catalog
  - `error_logger.py`, `ollama_client.py`, `non_pii_filter.py`, `text_normalizer.py`
- `app/utils/`:
  - `device.py` — CPU/MPS/CUDA selection
  - `crypto.py` — AES-256-GCM file encryption
  - `text_utils.py` — Unicode NFC + locale-aware lowercasing
- `tests/` — pytest scenarios (sanitizer, anonymization_map, national_ids, policy_composer, deanonymizer, llm_router, crypto, ingesters, etc.).

With FastAPI we follow Context7 best practices:

- API endpoints are modularised with **APIRouter**.
- Request/response validation uses Pydantic models.
- DB session, settings and other dependencies are injected via `Depends(...)`.
- All path functions are async; CPU-bound tasks run in a thread pool.

---

## Frontend (Next.js App Router) Structure

Frontend root: `frontend/`

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
- Docker Compose (PostgreSQL 16 + Redis 7 + backend + frontend)
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

- Python 3.10+
- Node.js 18+ (for Next.js 16)
- ffmpeg (for Whisper)

#### 2. Backend setup

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
```

All configuration is handled by the setup wizard on first run. A `config.json` file is auto-generated in `backend/` with encryption keys and infrastructure settings. No manual configuration files needed.

Then start the backend:

```bash
uvicorn app.main:app --reload
```

#### 3. Frontend setup

```bash
cd frontend
npm install
npm run dev
```

By default:
- Backend: `http://localhost:8000`
- Frontend: `http://localhost:3000`

Ensure the backend base URL in `src/lib/api.ts` matches your environment.

**First launch:** The web UI runs a short setup wizard (LLM provider and connection test) until onboarding is marked complete in application settings. Chat conversations are persisted server-side (`/api/chat-sessions`) and can be switched from the chat sidebar.

---

## Running Tests

The project includes a custom `/test` rule inside Septum:

- Based on the changed file, the corresponding pytest file is executed. Examples:
  - `sanitizer.py` → `tests/test_sanitizer.py`
  - `anonymization_map.py` → `tests/test_anonymization_map.py`
  - `app/services/national_ids/*` → `tests/test_national_ids.py`
  - `app/services/ingestion/*` → `tests/test_ingesters.py`
  - etc.
- If no match is found, the full test suite is executed.

**Continuous integration:** GitHub Actions runs backend tests plus Ruff and Bandit, `pip-audit`, and frontend Jest, `tsc --noEmit`, and `npm audit` in parallel on every push and pull request.

To run tests manually:

```bash
cd backend
pytest tests/ -v
```

Any tests that would send real requests to a cloud LLM **must be mocked**; tests that hit real external LLM APIs are treated as bugs.

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

- **Events tracked:** PII detection (per entity type and count), de-anonymisation, document upload/delete, regulation changes.
- **No raw PII stored:** Audit events record entity type names and counts only — never original values.
- **REST API:**
  - `GET /api/audit` — paginated, filterable by event type, document, session, and date range.
  - `GET /api/audit/{document_id}/report` — compliance report for a specific document.
  - `GET /api/audit/session/{session_id}` — full audit trail for a chat session.
  - `GET /api/audit/metrics` — aggregate PII detection quality metrics (entity type distribution, coverage ratios, per-document averages).
- **Frontend:** Audit log viewer in Settings → Audit Trail with event type badges, entity breakdowns, and pagination.

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
| **Audit** | `GET /api/audit`, `GET /api/audit/{id}/report`, `GET /api/audit/metrics` | Compliance audit trail and detection metrics |
| **Health** | `GET /health`, `GET /metrics` | System health and Prometheus metrics |

Full OpenAPI schema is available at `http://localhost:8000/docs` when the backend is running.

---

## Roadmap & Extensibility

- Add new country regulations by creating new regulation packs in the recogniser registry.
- Add new national ID formats by adding validators and recognisers in the national ID module.
- Add new document formats by implementing dedicated ingesters in the ingestion layer.
- Update NER model mappings from the Settings → NER Models screen.
- Pronoun coreference resolution via local LLM (Ollama) detects implied subject references.
- PII detection quality metrics for data-driven assessment of detection coverage.
