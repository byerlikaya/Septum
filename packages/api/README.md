# septum-api

Air-gapped FastAPI REST layer for [Septum](https://github.com/byerlikaya/Septum).
Hosts the bootstrap configuration, SQLAlchemy models, and FastAPI
application scaffolding that together form the server-side half of the
privacy-first middleware. All PII detection and masking is delegated to
[`septum-core`](../core/) — this package never performs anonymization on
its own, and it never reaches the public internet. Outbound LLM traffic
lives in `septum-gateway` behind the message queue.

> **Status:** Phase 3a of the modular refactor. The package currently
> owns `bootstrap`, `config`, `database`, `models`, `seeds`, and `utils`;
> routers, services, and the FastAPI app object are still in
> `backend/app/` and will migrate in Phase 3b. `backend/app/` ships
> backward-compatibility shims so existing imports keep working.

## Installation

```bash
pip install -e packages/core
pip install -e packages/api
```

## What's included in Phase 3a

| Module | Description |
|---|---|
| `septum_api.bootstrap` | `config.json` reader/writer with auto-generated encryption and JWT secrets. |
| `septum_api.config` | Synchronous `get_settings()` helper for scripts. |
| `septum_api.database` | Lazy async SQLAlchemy engine, SQLite WAL tuning, seed defaults. |
| `septum_api.models` | ORM base plus `AppSettings`, `User`, `Document`, `ChatSession`, `RegulationRuleset`, etc. |
| `septum_api.seeds` | Built-in regulation ruleset seed data. |
| `septum_api.utils` | Crypto (AES-256-GCM), JWT auth dependency, Prometheus metrics, structured logging. |

Routers, services, middleware, and the `FastAPI()` app instance arrive in
Phase 3b together with the test suite migration.

## Usage (programmatic)

```python
from septum_api import bootstrap
from septum_api.database import build_database_url, initialize_engine

config = bootstrap.get_config()
initialize_engine(build_database_url(config.database_url, config.db_path))
```

## Zone rules

- **Never import network libraries** (`requests`, `httpx`, `urllib`) —
  septum-api lives in the air-gapped zone.
- **Never import from `septum-gateway`** — the dependency arrow only runs
  the other way through `septum-queue`.
- `septum-core` is the only PII-aware dependency — all detection,
  masking, and unmasking go through it.
