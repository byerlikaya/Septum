# septum-api

Air-gapped FastAPI REST layer for [Septum](https://github.com/byerlikaya/Septum).
Hosts the bootstrap configuration, SQLAlchemy models, routers, services,
middleware, and the FastAPI app object that together form the
server-side half of the privacy-first middleware. All PII detection and
masking is delegated to [`septum-core`](../core/) — this package never
performs anonymization on its own, and it never reaches the public
internet. Outbound LLM traffic lives in
[`septum-gateway`](../gateway/) behind the message queue.

## Installation

```bash
pip install -e packages/core
pip install -e packages/queue
pip install -e "packages/api[auth,rate-limit,postgres,server,test]"
# Heavy ML / ingestion / OCR / Whisper deps live in requirements.txt:
pip install -r packages/api/requirements.txt
```

## Package layout

| Module | Description |
|---|---|
| `septum_api.bootstrap` | `config.json` reader/writer with auto-generated encryption and JWT secrets. |
| `septum_api.config` | Synchronous `get_settings()` helper for scripts. |
| `septum_api.database` | Lazy async SQLAlchemy engine, SQLite WAL tuning, seed defaults. |
| `septum_api.main` | FastAPI app factory + lifespan + middleware wiring + OpenAPI customization. |
| `septum_api.models` | ORM base plus `AppSettings`, `User`, `Document`, `ChatSession`, `RegulationRuleset`, `ApiKey`, `AuditEvent`, `EntityDetection`, `ErrorLog`. |
| `septum_api.routers` | 14 APIRouter modules (`auth`, `api_keys`, `chat`, `chat_sessions`, `chunks`, `documents`, `regulations`, `settings`, `setup`, `users`, `approval`, `audit`, `error_logs`, `text_normalization`). |
| `septum_api.services` | Document pipeline, sanitizer wrapper, `llm_router`, `vector_store`, `bm25_retriever`, `deanonymizer`, `prompts`, `approval_gate`, `gateway_client` (queue producer), `ingestion/`, `llm_providers/`. |
| `septum_api.middleware` | `auth.py` (JWT + API key resolution) and `rate_limit.py` (slowapi + per-route limits). |
| `septum_api.utils` | Crypto (AES-256-GCM), `auth_dependency`, Prometheus metrics, structured logging. |
| `septum_api.seeds` | Built-in regulation ruleset seed data. |
| `alembic/`, `alembic.ini` | Postgres schema migrations. Run with `alembic upgrade head` from `packages/api/`. |
| `scripts/docker-entrypoint.sh` | Container entrypoint: bootstrap config → alembic upgrade → uvicorn. |
| `requirements.txt` | Heavy ML / OCR / Whisper / ingestion deps (torch, Presidio, spaCy, PaddleOCR, Whisper, FAISS, BM25, langchain, …). Not in `pyproject.toml` so `pip install septum-api` stays lean. |

## Usage (programmatic)

```python
from septum_api import bootstrap
from septum_api.database import build_database_url, initialize_engine
from septum_api.main import app  # FastAPI instance

config = bootstrap.get_config()
initialize_engine(build_database_url(config.database_url, config.db_path))
# uvicorn septum_api.main:app
```

## Zone rules

- **Never import network libraries** (`requests`, `httpx`, `urllib`) —
  septum-api lives in the air-gapped zone. Cloud LLM calls go over the
  queue to `septum-gateway`.
- **Never import from `septum-gateway`** — the dependency arrow only
  runs the other way through `septum-queue`.
- `septum-core` is the only PII-aware dependency — all detection,
  masking, and unmasking go through it.
