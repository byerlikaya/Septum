<p align="center">
  <img src="https://raw.githubusercontent.com/byerlikaya/Septum/main/assets/septum_logo.png" alt="Septum logo" width="180" />
</p>

<h3 align="center">Septum Gateway — cloud LLM forwarder (internet-facing zone)</h3>

<p align="center">
  <img src="https://img.shields.io/badge/zone-internet--facing-F59E0B" alt="Internet-facing zone" />
  <img src="https://img.shields.io/badge/no-septum--core-E53935" alt="Never imports septum-core" />
  <a href="https://github.com/byerlikaya/Septum"><img src="https://img.shields.io/badge/GitHub-Source-181717?logo=github" alt="GitHub" /></a>
  <a href="https://github.com/byerlikaya/Septum/blob/main/docs/ARCHITECTURE.md"><img src="https://img.shields.io/badge/docs-Architecture-blue" alt="Architecture" /></a>
</p>

---

Stateless bridge between the air-gapped Septum backend and cloud LLM providers (Anthropic / OpenAI / OpenRouter). Consumes **already-masked** requests from `septum-queue`, forwards them, publishes masked responses back.

**Security invariant.** The image does not contain `septum-core` and cannot import it — enforced at the Dockerfile layer (the COPY step never references `packages/core/`). The gateway only sees placeholder-masked text; raw PII never crosses the zone boundary.

Pair with [`septum-audit`](https://hub.docker.com/r/byerlikaya/septum-audit) for compliance logging.

## Quick start

```bash
docker compose -f docker-compose.gateway.yml up
```

Brings up the gateway worker + health endpoint + audit worker + audit API + Redis. Requires `.env` with `REDIS_PASSWORD` and your cloud LLM provider keys.

## Two containers from the same image

| Command | Role |
|---|---|
| `python -m septum_gateway` | Stdio worker — pulls off the queue, forwards to the LLM, publishes the answer envelope |
| `uvicorn septum_gateway.main:create_app --factory` | HTTP server — exposes `/health` for orchestrators (K8s, compose healthcheck) |

## Tags

| Tag | Contents |
|---|---|
| `latest`, `1`, `1.0`, `1.0.0` | Multi-arch (amd64 + arm64), ~100 MB — no torch, no Presidio, no spaCy |

## Links

- **Source:** [github.com/byerlikaya/Septum](https://github.com/byerlikaya/Septum)
- **Architecture (zones):** [docs/ARCHITECTURE.md](https://github.com/byerlikaya/Septum/blob/main/docs/ARCHITECTURE.md)
- **Deployment topologies:** [docs/ARCHITECTURE.md#deployment-topologies](https://github.com/byerlikaya/Septum/blob/main/docs/ARCHITECTURE.md#deployment-topologies)
