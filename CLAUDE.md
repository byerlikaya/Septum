# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Septum is a privacy-first AI middleware that lets organizations use their own documents with LLMs without exposing raw personal data to the cloud. Documents are locally anonymized (PII detected and masked), questions are answered against anonymized content, and answers are de-anonymized locally before display. No raw PII ever leaves the machine.

## Commands

### Development
```bash
./dev.sh --setup          # First-time: install backend (pip) + frontend (npm) deps
./dev.sh                  # Start backend (port 8000) + frontend (port 3000)
```

### Backend (from `backend/`)
```bash
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000   # API server
pytest                                          # All tests
pytest tests/test_crypto.py -v --tb=short       # Single test file
pytest tests/ --cov=app --cov-report=term-missing  # Tests with coverage
```

### Frontend (from `frontend/`)
```bash
npm run dev       # Dev server (webpack, 4GB heap)
npm run build     # Production build
npm run lint      # ESLint
npm test          # Jest tests
npm test -- --runInBand   # Sequential tests (CI mode)
```

### Docker
```bash
docker run -p 3000:3000 -p 8000:8000 -v septum-data:/app/data -v septum-uploads:/app/uploads -v septum-anon-maps:/app/anon_maps byerlikaya/septum
docker compose up                                          # With PostgreSQL + Redis
docker compose --profile ollama up                         # With local Ollama models
```

## Architecture

**Backend:** FastAPI + SQLAlchemy (async SQLite) + Presidio + spaCy/Transformers NER + FAISS + BM25

**Frontend:** Next.js 16 App Router + React 19 + TypeScript + Tailwind CSS + Axios

### Document Processing Pipeline
Upload → content-based type detection (python-magic) → format-specific ingester → language detection → PII sanitization (Presidio + NER + optional Ollama) → anonymization map created → semantic chunking → FAISS vector embedding + BM25 indexing → encrypted storage on disk

### Chat Flow
User query → sanitize query → hybrid retrieval (FAISS + BM25) → optional approval gate → send masked context to cloud LLM → de-anonymize response locally → stream to user via SSE

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

### Key Frontend Structure
- `src/app/**/page.tsx` — Route pages (chat, documents, chunks, settings); composition only
- `src/components/` — Stateless UI organized by feature (chat, documents, chunks, settings, layout)
- `src/lib/api.ts` — Centralized typed Axios client; no direct fetch/axios in components
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

When running tests after a change, target the relevant test file based on what was modified:
- `sanitizer.py` → `test_sanitizer.py`
- `anonymization_map.py` → `test_anonymization_map.py`
- `national_ids/` → `test_national_ids.py`
- `ingestion/` → `test_ingesters.py`
- `policy_composer.py` → `test_policy_composer.py`
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

All LLM calls in tests must be mocked — never send real requests to cloud LLMs.

## Git & Commit Workflow

- **Analyze and group changes** before committing — one logical change per commit.
- **Commit messages in English**, imperative style ("Add feature", "Fix bug").
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
- Verify version numbers against `frontend/package.json` and `backend/requirements.txt`.

## Regulation Entity Sources

- `backend/docs/REGULATION_ENTITY_SOURCES.md` documents the legal basis for each regulation's entity types.
- When changing entity types for a built-in regulation (in `database.py` seed or recognizer packs), update this doc in the same commit with the legal basis (article/section/recital).

## Dependency Freshness

- Always use latest stable versions for all JS/TS and Python dependencies.
- When editing `package.json` or `requirements.txt`, proactively update any outdated deps.
- Changes must result in 0 errors, 0 warnings after build/test.

## Adding New Components

Skill templates in `.claude/skills/` (also mirrored in `.cursor/rules/` for Cursor IDE):
- **`/new-regulation`** — recognizer pack + seed data + tests
- **`/new-recognizer`** — national ID validator + Presidio recognizer + tests
- **`/new-ingester`** — format extractor + MIME registration + tests

## Security Scan

Invoke `/security-scan` for a comprehensive audit (`.claude/skills/security-scan.md`):
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

**Docker (recommended):** No `.env` needed. Run `docker run -p 3000:3000 -p 8000:8000 byerlikaya/septum` and the setup wizard configures everything (database, cache, LLM provider).

**Local development:** Run `./dev.sh --setup` then `./dev.sh`. Bootstrap auto-generates `config.json` with encryption key and JWT secret. Set `SEPTUM_CONFIG_PATH` to override the default `/app/data/config.json` location. Environment variables (`DATABASE_URL`, `REDIS_URL`, `ENCRYPTION_KEY`, etc.) override `config.json` values.

**docker-compose:** No `.env` needed. `docker compose up` starts PostgreSQL + Redis + Septum with sensible defaults. Database and Redis URLs are wired in `docker-compose.yml`.

## CI/CD

GitHub Actions (`.github/workflows/tests.yml`): runs backend pytest (Python 3.13) and frontend Jest (Node 22) in parallel on push to main and PRs. Both must pass.
