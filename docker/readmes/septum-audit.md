<p align="center">
  <img src="https://raw.githubusercontent.com/byerlikaya/Septum/main/assets/septum_logo.png" alt="Septum logo" width="180" />
</p>

<h3 align="center">Septum Audit — compliance log + SIEM exporters (internet-facing zone)</h3>

<p align="center">
  <img src="https://img.shields.io/badge/zone-internet--facing-F59E0B" alt="Internet-facing zone" />
  <img src="https://img.shields.io/badge/no-septum--core-E53935" alt="Never imports septum-core" />
  <a href="https://github.com/byerlikaya/Septum"><img src="https://img.shields.io/badge/GitHub-Source-181717?logo=github" alt="GitHub" /></a>
  <a href="https://github.com/byerlikaya/Septum/blob/main/docs/ARCHITECTURE.md"><img src="https://img.shields.io/badge/docs-Architecture-blue" alt="Architecture" /></a>
</p>

---

Append-only JSONL audit sink with JSON / CSV / Splunk HEC exporters. Consumes audit events from `septum-queue` and writes them to durable storage with retention policies.

**Security invariant.** The image does not contain `septum-core` and cannot import it — audit events carry only entity types, counts, regulation ids, and timestamps; never raw PII.

Pair with [`septum-gateway`](https://hub.docker.com/r/byerlikaya/septum-gateway) for the internet-facing zone, or run against the all-in-one [`septum`](https://hub.docker.com/r/byerlikaya/septum) image for audit-only deployments.

## Quick start

```bash
docker compose -f docker-compose.gateway.yml up
```

Brings up the audit worker + audit HTTP API + the gateway pair + Redis. Retention window, sink paths, and SIEM endpoints configured via env.

## Two containers from the same image

| Command | Role |
|---|---|
| `python -m septum_audit` | Stdio worker — consumes audit events from the queue, writes JSONL + applies retention |
| `uvicorn septum_audit.main:create_app --factory` | HTTP server — `/health` + `/api/audit/export` (JSON / CSV / Splunk HEC) |

## Tags

| Tag | Contents |
|---|---|
| `latest`, `1`, `1.0`, `1.0.0` | Multi-arch (amd64 + arm64), ~100 MB |

## Links

- **Source:** [github.com/byerlikaya/Septum](https://github.com/byerlikaya/Septum)
- **Architecture:** [docs/ARCHITECTURE.md](https://github.com/byerlikaya/Septum/blob/main/docs/ARCHITECTURE.md)
- **Audit & compliance:** [docs/ARCHITECTURE.md#audit-trail--compliance-reporting](https://github.com/byerlikaya/Septum/blob/main/docs/ARCHITECTURE.md#audit-trail--compliance-reporting)
