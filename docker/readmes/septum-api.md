<p align="center">
  <img src="https://raw.githubusercontent.com/byerlikaya/Septum/main/assets/septum_logo.png" alt="Septum logo" width="180" />
</p>

<h3 align="center">Septum API — FastAPI backend (air-gapped zone)</h3>

<p align="center">
  <img src="https://img.shields.io/badge/zone-air--gapped-1E88E5" alt="Air-gapped zone" />
  <a href="https://github.com/byerlikaya/Septum"><img src="https://img.shields.io/badge/GitHub-Source-181717?logo=github" alt="GitHub" /></a>
  <a href="https://github.com/byerlikaya/Septum/blob/main/docs/ARCHITECTURE.md"><img src="https://img.shields.io/badge/docs-Architecture-blue" alt="Architecture" /></a>
  <a href="https://github.com/byerlikaya/Septum/blob/main/CHANGELOG.md"><img src="https://img.shields.io/badge/docs-Changelog-blue" alt="Changelog" /></a>
</p>

---

FastAPI REST layer for Septum. Runs the document pipeline, PII masking, chat/SSE endpoints, auth, rate limiting, and audit-event emission. **Air-gapped zone** — no outbound internet by design; LLM calls go out via `septum-gateway` when `USE_GATEWAY=true`.

Pair with [`septum-web`](https://hub.docker.com/r/byerlikaya/septum-web) for the dashboard. For the all-in-one image see [`septum`](https://hub.docker.com/r/byerlikaya/septum).

## Quick start (docker compose, air-gapped topology)

Copy [`.env.example`](https://github.com/byerlikaya/Septum/blob/main/.env.example) → `.env`, set `POSTGRES_PASSWORD` and `REDIS_PASSWORD`, then:

```bash
docker compose -f docker-compose.airgap.yml up
```

This brings up `septum-api` + `septum-web` + PostgreSQL + Redis on a single host with no cloud egress.

## Standalone run

```bash
docker run --name septum-api -p 8000:8000 \
  -e DATABASE_URL=postgresql+asyncpg://septum:<pw>@<host>:5432/septum \
  -e REDIS_URL=redis://:<pw>@<host>:6379/0 \
  byerlikaya/septum-api
```

Health check: `curl http://localhost:8000/health`. OpenAPI at `/docs`, ReDoc at `/redoc`.

## Tags

| Tag | Contents |
|---|---|
| `latest`, `1`, `1.0`, `1.0.0` | CPU variant, multi-arch (amd64 + arm64) |
| `gpu`, `1-gpu`, `1.0-gpu`, `1.0.0-gpu` | CUDA-enabled PyTorch, linux/amd64 only |

## Links

- **Source:** [github.com/byerlikaya/Septum](https://github.com/byerlikaya/Septum)
- **Architecture:** [docs/ARCHITECTURE.md](https://github.com/byerlikaya/Septum/blob/main/docs/ARCHITECTURE.md)
- **Deployment topologies:** [docs/ARCHITECTURE.md#deployment-topologies](https://github.com/byerlikaya/Septum/blob/main/docs/ARCHITECTURE.md#deployment-topologies)
