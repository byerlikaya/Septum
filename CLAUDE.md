# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Septum is a privacy-first AI middleware that lets organizations use their own documents with LLMs without exposing raw personal data to the cloud. Documents are locally anonymized (PII detected and masked), questions are answered against anonymized content, and answers are de-anonymized locally before display. No raw PII ever leaves the machine.

## Working Rules (CRITICAL)

- Before starting each new phase, run `/simplify` first, then `/compact` — clear context from the previous phase
- Never commit on your own. `git add` and `git commit` only when the user explicitly says "commit et" or "commit it"
- Before committing, show the commit message first, get approval, then execute
- `git push` follows the same rule — only when the user asks

## Commands

### Development
```bash
./dev.sh --setup          # First-time: install backend (pip) + frontend (npm) deps
./dev.sh                  # Start dev servers (port 3000)
```

### Backend (from `packages/api/`)
```bash
python -m uvicorn septum_api.main:app --reload --host 0.0.0.0 --port 8000   # API server
pytest tests/                                   # All tests
pytest tests/test_crypto.py -v --tb=short       # Single test file
pytest tests/ --cov=septum_api --cov-report=term-missing  # Tests with coverage
```

### Frontend (from `packages/web/`)
```bash
npm run dev       # Dev server (webpack, 4GB heap)
npm run build     # Production build
npm run lint      # ESLint
npm test          # Jest tests
npm test -- --runInBand   # Sequential tests (CI mode)
```

Set `NEXT_PUBLIC_API_BASE_URL` at build time to point the dashboard at a backend on a different origin (split deployment). Unset → relative URLs proxied via Next.js rewrites (single-container default).

### Docker
```bash
# Published standalone image (SQLite, no external services)
docker run --name septum --add-host=host.docker.internal:host-gateway -p 3000:3000 -v septum-data:/app/data -v septum-uploads:/app/uploads -v septum-anon-maps:/app/anon_maps -v septum-vector-indexes:/app/vector_indexes -v septum-bm25-indexes:/app/bm25_indexes -v septum-models:/app/models byerlikaya/septum

# Full dev stack (all modules + Postgres + Redis + Ollama)
docker compose up
docker compose exec ollama ollama pull llama3.2:3b         # first-time: pull a model
docker compose -f docker-compose.yml -f docker-compose.no-ollama.yml up   # skip Ollama (cloud provider only)

# Modular topologies (build from docker/ Dockerfiles)
docker compose -f docker-compose.standalone.yml up         # Single container, SQLite
docker compose -f docker-compose.airgap.yml up             # Air-gapped zone (api + web + postgres + redis)
docker compose -f docker-compose.gateway.yml up            # Internet-facing zone (gateway + audit + redis)
```

### Modular Packages (from `packages/<module>/`)
```bash
pip install -e packages/core              # Install septum-core in editable mode
pip install -e packages/mcp               # Install septum-mcp in editable mode
pip install -e packages/queue             # Install septum-queue (file backend, stdlib only)
pip install -e "packages/queue[redis]"    # septum-queue with Redis Streams backend
pip install -e packages/gateway           # Install septum-gateway (consumer + forwarders)
pip install -e "packages/gateway[server]" # Adds FastAPI /health endpoint
pip install -e packages/audit             # Install septum-audit (records, sinks, exporters, retention)
pip install -e "packages/audit[queue]"    # Adds AuditConsumer (depends on septum-queue)
pip install -e "packages/audit[server]"   # Adds FastAPI /health + /api/audit/export
pytest packages/core/tests/               # Run core tests
pytest packages/mcp/tests/                # Run MCP tests
pytest packages/queue/tests/              # Run queue tests (uses fakeredis when [redis] is installed)
pytest packages/gateway/tests/            # Run gateway tests (uses respx for httpx mocking)
pytest packages/audit/tests/              # Run audit tests (sinks, exporters, retention, consumer, FastAPI)
```

## Architecture

**Backend:** FastAPI + SQLAlchemy (async SQLite) + Presidio + spaCy/Transformers NER + FAISS + BM25

**Frontend:** Next.js 16 App Router + React 19 + TypeScript + Tailwind CSS + Axios

### Document Processing Pipeline
Upload → content-based type detection (python-magic) → format-specific ingester → language detection → PII sanitization (Presidio + NER + optional Ollama) → anonymization map created → semantic chunking → FAISS vector embedding + BM25 indexing → encrypted storage on disk

### Chat Flow
User query → sanitize query → hybrid retrieval (FAISS + BM25) → optional approval gate → send masked context to cloud LLM → de-anonymize response locally → stream to user via SSE

### Modular Architecture (refactor/modular-architecture branch)

The monolithic structure is being split into 7 independent modules. See `PROJECT_SPEC.md` for full details.

| Module | Location | Zone | Description |
|---|---|---|---|
| **septum-core** | `packages/core/` | Air-gapped | PII detection, masking, unmasking, regulation engine |
| **septum-mcp** | `packages/mcp/` | Air-gapped | MCP server for Claude Code/Desktop integration |
| **septum-api** | `packages/api/` | Air-gapped | FastAPI REST endpoints |
| **septum-web** | `packages/web/` | Air-gapped | Next.js dashboard + approval UI |
| **septum-queue** | `packages/queue/` | Bridge | Cross-zone message broker (masked data only) |
| **septum-gateway** | `packages/gateway/` | Internet-facing | Cloud LLM forwarder |
| **septum-audit** | `packages/audit/` | Internet-facing | Compliance logging + SIEM export |

#### Module Import Rules (CRITICAL)

```python
# septum-core: NEVER import network libraries
# ❌ import requests, import httpx, import urllib
# ✅ only: presidio, transformers, regex, pydantic

# septum-gateway: NEVER import septum-core (it must never see raw PII)
# ❌ from septum_core import SeptumEngine
# ✅ from septum_queue import QueueConsumer

# septum-mcp: CAN use septum-core
# ✅ from septum_core import SeptumEngine

# septum-api: CAN use septum-core + septum-queue
# ✅ from septum_core import SeptumEngine
# ✅ from septum_queue import QueueProducer
```

#### Zone Rules

- **Air-gapped zone** modules (core, mcp, api, web): zero internet access, all PII operations here
- **Internet-facing zone** modules (gateway, audit): zero PII access, only sees masked placeholders
- **Bridge** (queue): transports only masked data between zones, raw PII never crosses

#### Dependency Graph

```
septum-core ← standalone, zero network deps
    ↑
septum-mcp ← depends on septum-core
    ↑
septum-api ← depends on septum-core, septum-queue (optional)
    ↑
septum-web ← depends on septum-api (HTTP only, runtime)

septum-queue ← standalone, abstract interface

septum-gateway ← depends on septum-queue (NEVER on septum-core)
septum-audit ← depends on septum-queue (optional, event consumer)
```

### Key Backend Services
- `bootstrap.py` — Infrastructure config (`config.json`): encryption key, JWT secret, DB/Redis URLs. Auto-generates secrets on first run. Env vars override file values.
- `database.py` — Lazy async engine. `initialize_engine()` called from lifespan or setup wizard. `get_db()` returns 503 if engine not ready.
- `routers/setup.py` — Setup wizard API: `/api/setup/status`, `/api/setup/initialize`, `/api/setup/test-database`, `/api/setup/test-redis`. No auth, no DB dependency.
- `services/sanitizer.py` — Main anonymization engine (3-layer: Presidio, HuggingFace NER, optional Ollama)
- `services/anonymization_map.py` — Session-scoped PII↔placeholder mapping with coreference resolution
- `services/deanonymizer.py` — Re-maps placeholders back to originals
- `services/policy_composer.py` — Merges active regulation rulesets into unified detection pipeline
- `services/prompts.py` — `PromptCatalog`: all LLM prompts centralized here, never inline
- `services/llm_router.py` — Provider dispatch (OpenAI, Anthropic, Ollama)
- `services/document_pipeline.py` — End-to-end document processing orchestration
- `services/vector_store.py` — FAISS vector DB + retrieval
- `services/bm25_retriever.py` — BM25 keyword search for hybrid retrieval
- `services/recognizers/{regulation_id}/` — Per-regulation Presidio recognizer packs
- `services/national_ids/` — Country-specific ID validators with algorithmic checksums
- `services/ingestion/` — Format-specific document extractors (PDF, DOCX, XLSX, images/OCR, audio/Whisper, etc.)
- `utils/crypto.py` — AES-256-GCM encryption for files at rest

### Key Frontend Structure (in `packages/web/`)
- `src/app/**/page.tsx` — Route pages (chat, documents, chunks, settings); composition only
- `src/components/` — Stateless UI organized by feature (chat, documents, chunks, settings, layout)
- `src/lib/api.ts` — Centralized typed Axios client; no direct fetch/axios in components. `baseURL` resolves from `NEXT_PUBLIC_API_BASE_URL` at build time, defaults to `""` (same-origin proxy via Next.js rewrites)
- `src/lib/types.ts` — Shared TypeScript interfaces
- `src/store/` — Shared state hooks (chat, documents, settings, regulations)
- `src/i18n/` — Translations (English default + Turkish + extensible)

## Core Principles

1. **Raw PII never leaves the machine.** Anonymization maps are never sent to frontend, cloud, or logs. Files stored encrypted (AES-256-GCM).
2. **Globally generic.** No hardcoded logic for specific countries, languages, questions, or document types. Use mapping tables and DB-driven config, not `if language == "tr"` branches. Country/language names forbidden in class/function/variable names (exceptions: `national_ids/` folder, model IDs, regulation seed data, tests).
3. **Multi-regulation composition.** Multiple regulation rulesets active simultaneously; sanitizer applies union of all, most restrictive rule wins.
4. **Centralized prompts.** All LLM prompts via `PromptCatalog` in `services/prompts.py`, never inline strings.
5. **Content-based file detection.** Use python-magic, never file extensions.
6. **All user-facing strings use i18n** (`useI18n()` hook in frontend).
7. **Async-first.** All DB, file I/O, and LLM calls are async.
8. **No unnecessary comments.** Docstrings required, but redundant/obvious/decorative comments must be avoided.
9. **Modular isolation.** Each package in `packages/` is independently installable. Internet-facing modules never import septum-core. All PII operations happen exclusively in septum-core.

## Zero-Tolerance Generic Architecture

These patterns are **forbidden** in production code (tests and `national_ids/` are exceptions):
- Country/language names in class/function/variable names (`TurkishPhoneRecognizer` → `ExtendedPhoneRecognizer`)
- Hardcoded text patterns or term lists (`["MADDE", "Article"]` → structural detection)
- Language-specific `if` branches (`if language == "tr"` → use mapping tables like `LOCALE_CASING_RULES.get(lang)`)
- Hardcoded stopwords (move to DB or keep minimal with `# FUTURE: move to DB`)

**Allowed exceptions:** ISO 639-1 codes in mapping tables, HuggingFace model IDs, `national_ids/` algorithmic validators, regulation seed descriptions in `database.py`, test files.

## Multi-Regulation System

17 built-in regulation packs (GDPR, KVKK, CCPA, CPRA, HIPAA, PIPEDA, LGPD, PDPA_TH, PDPA_SG, APPI, PIPL, POPIA, DPDP, UK_GDPR, PDPL_SA, NZPA, Australia_PA). Each is a recognizer pack in `services/recognizers/{regulation_id}/`. Users can also create custom rulesets with regex, keyword list, or LLM-prompt detection methods.

## Smart Test Runner

When running tests after a change, target the relevant test file based on what was modified. Tests live under `packages/<name>/tests/`.

septum-api (`packages/api/tests/`):
- `sanitizer.py` → `test_sanitizer.py`
- `anonymization_map.py` → `test_anonymization_map.py`
- `national_ids/` → `test_national_ids.py`
- `ingestion/` → `test_ingesters.py`
- `policy_composer.py` → `test_policy_composer_api.py`
- `crypto.py` → `test_crypto.py`
- `llm_router.py` → `test_llm_router.py`
- `deanonymizer.py` → `test_deanonymizer.py`
- `vector_store.py` → `test_vector_store.py`
- `document_pipeline.py` → `test_document_pipeline.py`
- `document_anon_store.py` → `test_document_anon_store.py`
- `non_pii_filter.py` → `test_non_pii_filter.py`
- `routers/chat.py` → `test_chat_sanitization.py`, `test_chat_context_prompt.py`
- `routers/approval.py` → `test_approval_router.py`
- `prompts.py` → `test_chat_context_prompt.py`

Other packages:
- `packages/core/septum_core/*.py` → `packages/core/tests/`
- `packages/mcp/septum_mcp/*.py` → `packages/mcp/tests/`
- `packages/queue/septum_queue/*.py` → `packages/queue/tests/`
- `packages/gateway/septum_gateway/*.py` → `packages/gateway/tests/`
- `packages/audit/septum_audit/*.py` → `packages/audit/tests/`

All LLM calls in tests must be mocked — never send real requests to cloud LLMs.

## Git & Commit Workflow

- **Never commit or push unless the user explicitly asks.** Show the commit message first, wait for approval.
- **Analyze and group changes** before committing — one logical change per commit.
- **Commit messages in English**, imperative style ("Add feature", "Fix bug").
- **Commit message format for modular refactoring:**
  ```
  <type>(<scope>): <description>

  type: feat, fix, refactor, test, docs, chore
  scope: core, mcp, api, web, queue, gateway, audit
  ```
  Examples: `refactor(core): extract detector from backend services`, `feat(mcp): add mask_text tool`
- **Never push** unless explicitly asked.
- **Scan for secrets** before committing — warn and block if credentials, API keys, or private keys are detected.
- **Never commit** build artifacts, logs, `config.json`, or machine-generated files.

## Changelog Maintenance

- `CHANGELOG.md` uses date-based sections (`### YYYY-MM-DD`). Always verify date with `date +%Y-%m-%d`.
- Group related changes as **logical development units** — one bullet per effort, not per commit. If a follow-up fix belongs to the same feature, append to the existing bullet.
- Update `CHANGELOG.md` in the same commit as the code change.

## README Synchronization

- `README.md` (English) and `README.tr.md` (Turkish) must always have identical sections, in the same order.
- Any change to one must be mirrored to the other in the same changeset.
- Verify version numbers against `packages/web/package.json` and `packages/api/requirements.txt`.

## Regulation Entity Sources

- `packages/core/docs/REGULATION_ENTITY_SOURCES.md` documents the legal basis for each regulation's entity types.
- When changing entity types for a built-in regulation (in `database.py` seed or recognizer packs), update this doc in the same commit with the legal basis (article/section/recital).

## Dependency Freshness

- Always use latest stable versions for all JS/TS and Python dependencies.
- When editing `package.json` or `requirements.txt`, proactively update any outdated deps.
- Changes must result in 0 errors, 0 warnings after build/test.

## Security Scan

Invoke `/security-scan` for a comprehensive audit (`.claude/skills/security-scan/SKILL.md`):
- Dependency audit (pip-audit, npm audit), OWASP Top 10, Septum-specific checks (PII in logs, crypto keys, anon map exposure), config scan
- Report format: `[SEVERITY] Title — file:LINE` with Issue/Impact/Fix
- Rules: never auto-install tools, mask secrets, never auto-fix without confirmation

## Automated Pre-Commit Checks

`.claude/pre-commit-check.sh` runs automatically before `git commit` and blocks if:
- Zero-tolerance violations (country/language names in production code)
- Language-specific if-branches detected
- CHANGELOG.md missing entry for today's date
- Secrets files (`config.json`) staged for commit
- README.md changed without README.tr.md (or vice versa)
- `database.py` entity_types changed without REGULATION_ENTITY_SOURCES.md update

## Environment Setup

**Docker (recommended):** No `.env` needed. Run `docker run --add-host=host.docker.internal:host-gateway -p 3000:3000 byerlikaya/septum` and the setup wizard configures everything (database, cache, LLM provider). Everything is served on port 3000 (API proxied via Next.js rewrites).

**Local development:** Run `./dev.sh --setup` then `./dev.sh`. Bootstrap auto-generates `config.json` with encryption key and JWT secret. Set `SEPTUM_CONFIG_PATH` to override the default `/app/data/config.json` location. Environment variables (`DATABASE_URL`, `REDIS_URL`, `ENCRYPTION_KEY`, etc.) override `config.json` values.

**docker-compose:** No `.env` needed. `docker compose up` starts PostgreSQL + Redis + Septum with sensible defaults. Database and Redis URLs are wired in `docker-compose.yml`.

**Modular docker-compose variants:**
- `docker-compose.yml` — Full dev stack (api + web + gateway + audit + Postgres + Redis + optional Ollama)
- `docker-compose.airgap.yml` — Air-gapped zone only (api + web + Postgres + Redis, `USE_GATEWAY_DEFAULT=true`)
- `docker-compose.gateway.yml` — Internet-facing zone only (gateway-worker + gateway-health + audit-worker + audit-api + Redis)
- `docker-compose.standalone.yml` — Single container from `docker/standalone.Dockerfile` (SQLite, simplest install)

**Per-module Dockerfiles:** `docker/api.Dockerfile`, `docker/web.Dockerfile`, `docker/gateway.Dockerfile`, `docker/audit.Dockerfile`, `docker/mcp.Dockerfile`, `docker/standalone.Dockerfile`. Gateway + audit images are lightweight (~100 MB, no torch/Presidio) and — by code-review invariant — never contain `septum-core`.

## CI/CD

GitHub Actions (`.github/workflows/tests.yml`): runs backend pytest (Python 3.13) and frontend Jest (Node 22) in parallel on push to main and PRs. Both must pass.

## Release process

Releases are driven by **git tags** — there is no `VERSION` file to bump and no auto-commit dance.

```bash
# 1. Update CHANGELOG.md with the release summary under today's date.
# 2. Tag the release (semver; pre-releases get a -rc.N / -beta.N suffix):
git tag v0.2.0
git push --tags

# Manual re-run against an existing tag: Actions UI → Docker Hub Publish → Run workflow → enter version.
```

`.github/workflows/docker-publish.yml` fires on the tag, builds all six images (`byerlikaya/septum`, `byerlikaya/septum-{api,web,gateway,audit,mcp}`) multi-arch (linux/amd64 + linux/arm64), pushes them to Docker Hub with rolling tags (`v0.2.0`, `0.2`, `0`, `latest`), stamps OCI image labels (`org.opencontainers.image.{version,revision,source,title,…}`), and creates a GitHub Release page with autogenerated release notes + image pull links.

**CPU / GPU variants:** `byerlikaya/septum` and `byerlikaya/septum-api` additionally publish a `-gpu` variant (`byerlikaya/septum:0.2.0-gpu`, `byerlikaya/septum:gpu`) built with the standard PyTorch wheel (full CUDA runtime, ~6 GB overhead). GPU variants are **linux/amd64 only** — NVIDIA wheels are not published for arm64 and Apple Silicon hosts do not run CUDA. Other images (web, gateway, audit, mcp) stay CPU-only because they either do not import torch at all or run as stdio subprocesses where GPU offers no practical benefit. Selection via Dockerfile `ARG TORCH_VARIANT=cpu` (default) or `=gpu` when building locally.

**Local dev (`./dev.sh`) is GPU-native:** `packages/api/requirements.txt` pins `torch==2.10.0` without a CPU-only index override, so a local dev install picks up whatever torch variant PyPI serves for the host platform — CUDA on NVIDIA-equipped Linux, MPS on Apple Silicon, CPU everywhere else. The CPU-only constraint only applies to the published Docker images.

Pre-releases (`v0.2.0-rc.1`) skip the rolling tags and `latest`; they publish only the exact version.
