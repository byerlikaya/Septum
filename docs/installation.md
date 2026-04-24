# Septum — Installation Guide

<p align="center">
  <a href="../readme.md"><strong>🏠 Home</strong></a>
  &nbsp;·&nbsp;
  <strong>🚀 Installation</strong>
  &nbsp;·&nbsp;
  <a href="benchmark.md"><strong>📈 Benchmark</strong></a>
  &nbsp;·&nbsp;
  <a href="features.md"><strong>✨ Features</strong></a>
  &nbsp;·&nbsp;
  <a href="architecture.md"><strong>🏗️ Architecture</strong></a>
  &nbsp;·&nbsp;
  <a href="document-ingestion.md"><strong>📊 Document Ingestion</strong></a>
  &nbsp;·&nbsp;
  <a href="screenshots.md"><strong>📸 Screenshots</strong></a>
</p>

---

## Table of Contents

- [⚡ Quickstart](#-quickstart)
- [System requirements](#system-requirements)
- [Installation variants](#installation-variants)
  - [Full local stack (recommended)](#1-full-local-stack--recommended)
  - [Standalone single container (demo)](#2-standalone-single-container--demo)
  - [Air-gapped zone only](#3-air-gapped-zone-only)
  - [Internet-facing zone only](#4-internet-facing-zone-only)
  - [Native / source install](#5-native--source-install-contributors)
- [First launch — setup wizard](#first-launch--setup-wizard)
- [LLM providers](#llm-providers)
- [Data persistence & volumes](#data-persistence--volumes)
- [Upgrading](#upgrading)
- [Troubleshooting](#troubleshooting)
- [Uninstalling](#uninstalling)

---

## ⚡ Quickstart

The shortest path to a working Septum. Three commands, five minutes, a local-first AI middleware with Ollama bundled and ready.

```bash
git clone https://github.com/byerlikaya/Septum.git && cd Septum
cp .env.example .env
# Open .env in your editor and set POSTGRES_PASSWORD + REDIS_PASSWORD
docker compose up
```

Open **http://localhost:3000** — the setup wizard takes you the rest of the way.

The compose stack brings up PostgreSQL, Redis, Ollama, the FastAPI backend, and the Next.js dashboard on a single host. The first run pulls all images from Docker Hub and a default Ollama model; subsequent runs are instant. No raw PII ever leaves the machine — detection, masking, and LLM inference all happen locally.

---

## System requirements

| Resource | Minimum | Recommended |
|---|---|---|
| CPU | 2 cores (x86-64 or arm64) | 4+ cores |
| RAM | 6 GB free | 16 GB (so Ollama can hold a 7B-class model) |
| Disk | 12 GB free | 30 GB (models + document indexes grow) |
| Docker | Desktop 4.30+ / Engine 24+ with Compose v2 | Latest stable |
| OS | macOS 13+, Windows 10/11 with WSL2, any mainstream Linux | — |

**Platform notes.**

- **Apple Silicon (M1/M2/M3/M4)** — fully supported. Ollama uses Metal acceleration automatically; no extra flags needed. Multi-arch Docker images ship an arm64 variant.
- **NVIDIA GPU (Linux amd64)** — optional. Use the `-gpu` tag of `byerlikaya/septum` / `byerlikaya/septum-api` for CUDA-accelerated PyTorch (faster OCR, Whisper, embedding). Ollama runs on the GPU automatically when one is visible inside the container.
- **Windows** — WSL2 is required; native Windows containers are not supported. All paths and volume examples assume a POSIX shell; run them from a WSL terminal.

---

## Installation variants

Five supported topologies. Pick the one that matches your deployment shape.

### 1. Full local stack — **recommended**

Single host, all modules plus Ollama, PostgreSQL, Redis. The default `docker-compose.yml` ships this topology.

```bash
git clone https://github.com/byerlikaya/Septum.git && cd Septum
cp .env.example .env
# Edit .env: set POSTGRES_PASSWORD and REDIS_PASSWORD (required)

docker compose up           # foreground — watch the first-boot logs
# or
docker compose up -d        # detached — release the terminal

# First-time: pull a default model into the bundled Ollama
docker compose exec ollama ollama pull llama3.2:3b
```

Navigate to **http://localhost:3000**. See the [First launch](#first-launch--setup-wizard) section for the wizard walkthrough.

To run without Ollama (cloud-provider-only deployments):

```bash
docker compose -f docker-compose.yml -f docker-compose.no-ollama.yml up
```

### 2. Standalone single container — **demo**

One container, SQLite, no external services. Fastest way to try Septum on a single laptop; ideal for sales demos and hobby deployments.

```bash
docker run --name septum \
  --add-host=host.docker.internal:host-gateway \
  -p 3000:3000 \
  -v septum-data:/app/data \
  -v septum-uploads:/app/uploads \
  -v septum-anon-maps:/app/anon_maps \
  -v septum-vector-indexes:/app/vector_indexes \
  -v septum-bm25-indexes:/app/bm25_indexes \
  -v septum-models:/app/models \
  byerlikaya/septum
```

Standalone does not bundle Ollama. Either run Ollama on the host (`brew install ollama && ollama serve`) and point the wizard at `http://host.docker.internal:11434`, or skip Ollama and use a cloud provider (Anthropic / OpenAI / OpenRouter).

**Use standalone when**: you want the fastest try-it-out experience, your corpus is small, and you don't need the full modular zone separation.

**Don't use standalone when**: you need air-gap separation, multi-user scale, or the full Septum feature set (semantic PII layer + Auto-RAG routing both want Ollama).

### 3. Air-gapped zone only

Runs only the air-gapped zone (api + web + PostgreSQL + Redis) on a host with no internet egress. Cloud LLM calls travel via a separate internet-facing host that hosts the gateway.

```bash
cp .env.example .env
# Set POSTGRES_PASSWORD + REDIS_PASSWORD

docker compose -f docker-compose.airgap.yml up -d
```

This topology sets `USE_GATEWAY_DEFAULT=true` so every LLM call is routed through `septum-queue` (Redis Streams) instead of HTTP. Only masked text crosses the zone boundary. Pair with variant 4 on the internet-facing host.

### 4. Internet-facing zone only

Runs only the gateway + audit modules on a host that *can* reach the cloud LLM providers. Consumes masked requests from Redis and forwards them to Anthropic / OpenAI / OpenRouter.

```bash
cp .env.example .env
# Set REDIS_PASSWORD (same value as the air-gapped host, over VPN / private net)
# Set provider keys: ANTHROPIC_API_KEY, OPENAI_API_KEY, etc.

docker compose -f docker-compose.gateway.yml up -d
```

The gateway image does not contain `septum-core` by security invariant — it only sees placeholder-masked text and publishes masked answers back. The audit sidecar logs every request with entity types, counts, and regulation ids (never raw PII) and exposes `/api/audit/export` for SIEM ingestion.

**Air-gapped + internet-facing together**: two Docker hosts, one Redis reachable from both (ideally over a VPN / private subnet). Set identical `REDIS_PASSWORD` on both hosts. Septum's encryption invariant guarantees that Redis sees only placeholder-masked payloads.

### 5. Native / source install (contributors)

For development, debugging, or custom builds. Not recommended for production — use a compose variant instead.

```bash
git clone https://github.com/byerlikaya/Septum.git && cd Septum
./dev.sh --setup        # installs all six Python packages + npm deps
./dev.sh                # starts api + web on port 3000
```

Requirements: Python 3.11+ (3.13 tested), Node.js 20+, ffmpeg (for Whisper audio ingestion). The bootstrap script writes `config.json` with a freshly generated encryption key and JWT secret on first run; override with `SEPTUM_CONFIG_PATH`.

Run the test suites:

```bash
pytest packages/*/tests -q        # all 7 modules
cd packages/web && npm test       # frontend Jest suite
```

---

## First launch — setup wizard

After any compose variant finishes booting, navigate to **http://localhost:3000**. You'll land on the setup wizard — it runs once, on a fresh install, and configures everything the backend needs to start.

| Step | What you do | Notes |
|---|---|---|
| **1. Database** | Pick SQLite (default) or PostgreSQL, test the connection | Compose variants pre-wire PostgreSQL; standalone defaults to SQLite |
| **2. Cache** | Pick in-memory (default) or Redis, test the connection | Compose variants pre-wire Redis; standalone uses in-memory |
| **3. LLM provider** | Ollama (local, bundled with full stack) / Anthropic / OpenAI / OpenRouter | See [LLM providers](#llm-providers) for key acquisition |
| **4. Regulations** | Tick the regulation packs you need (GDPR + your country, typically) | You can change this anytime via Settings → Regulations |
| **5. Audio model** | Whisper size (tiny / base / small / medium / large) | Skip if you won't ingest audio; downloads lazily on first audio upload |
| **6. Admin account** | Email + password for the first admin user | Becomes the only admin; create further accounts from the dashboard |

When the wizard finishes it writes `config.json`, creates the admin user, and redirects to the dashboard. The wizard is one-shot; hitting `/api/setup/*` after completion returns 403.

---

## LLM providers

Septum can use any combination of the following:

| Provider | Where to get a key | When to use |
|---|---|---|
| **Ollama** (local) | Bundled in the full stack; for standalone install from [ollama.com](https://ollama.com) | Semantic PII layer, Auto-RAG classifier, free-tier local chat |
| **Anthropic** | [console.anthropic.com](https://console.anthropic.com) | Claude Opus / Sonnet / Haiku — highest-quality answers |
| **OpenAI** | [platform.openai.com](https://platform.openai.com) | GPT-4, GPT-4 Turbo, GPT-4o |
| **OpenRouter** | [openrouter.ai](https://openrouter.ai) | Unified API across 100+ models with a single key |

**Ollama model recommendations:**

- `llama3.2:3b` — 2 GB, fast, good enough for Auto-RAG classification
- `aya-expanse:8b` — 4.7 GB, **recommended** for semantic PII detection; benchmark default
- `qwen2.5:14b` — 8.4 GB, better quality if you have the RAM

Pull additional models anytime:

```bash
docker compose exec ollama ollama pull aya-expanse:8b
docker compose exec ollama ollama list     # see what's downloaded
```

The active model for chat vs semantic detection is chosen from the Settings → LLM tab after the wizard.

---

## Data persistence & volumes

The compose stack declares named Docker volumes so your data survives container restarts and image upgrades:

| Volume | Path inside container | Content |
|---|---|---|
| `septum-data` | `/app/data` | SQLite DB (if used), `config.json`, encryption keys |
| `septum-uploads` | `/app/uploads` | Uploaded original files, AES-256-GCM encrypted |
| `septum-anon-maps` | `/app/anon_maps` | Per-document PII placeholder mappings, encrypted |
| `septum-vector-indexes` | `/app/vector_indexes` | FAISS embeddings |
| `septum-bm25-indexes` | `/app/bm25_indexes` | BM25 keyword indexes |
| `septum-models` | `/app/models` | Cached HuggingFace / Whisper models |
| `septum-postgres` | `/var/lib/postgresql/data` | PostgreSQL cluster (compose only) |
| `septum-redis` | `/data` | Redis AOF/RDB (compose only) |
| `septum-ollama` | `/root/.ollama` | Downloaded Ollama models |

**Backup.** A consistent backup means two things: a PostgreSQL dump *and* the encrypted volumes (uploads + anon-maps + vector/BM25 indexes). The encryption key lives in `septum-data/config.json` — lose it and every encrypted volume becomes unreadable.

```bash
# Dump Postgres
docker compose exec -T postgres pg_dump -U septum septum > septum-$(date +%F).sql

# Tar the named volumes
for v in data uploads anon-maps vector-indexes bm25-indexes models; do
  docker run --rm -v septum-$v:/src -v "$PWD":/dst alpine \
    tar czf /dst/septum-$v-$(date +%F).tar.gz -C /src .
done
```

**Restore** is the reverse. Stop the stack, `docker volume rm` the existing volumes, recreate + extract, start.

---

## Upgrading

Septum follows semver. Minor versions never break configs; major versions are called out in the [Changelog](../changelog.md) with explicit migration notes.

```bash
git pull                                    # compose file changes occasionally
docker compose pull                         # fetch new image tags from Docker Hub
docker compose up -d                        # recreate containers with the new images
docker compose logs -f api                  # watch the migration run
```

Alembic migrations apply automatically at api boot. If a migration fails, the api container exits with a descriptive error — inspect the logs, resolve the cause, retry the `up -d`.

For the standalone image:

```bash
docker stop septum && docker rm septum
docker pull byerlikaya/septum
# Re-run the same `docker run ...` you used initially
```

Volumes are preserved across the stop / rm / run cycle because they are named volumes.

---

## Troubleshooting

**Wizard hangs at "Testing Redis / PostgreSQL connection"**. The compose file requires `POSTGRES_PASSWORD` and `REDIS_PASSWORD` — if they're missing in `.env`, the data containers didn't start. Check `docker compose logs postgres redis` and set the variables before restarting.

**Ollama step fails with "connection refused"**. Full stack: the Ollama container may still be pulling its first model; give it 30-60 seconds and retry. Standalone: Ollama is not bundled — either install it on the host (`brew install ollama`) and point the wizard at `http://host.docker.internal:11434`, or choose a cloud provider.

**Port 3000 already in use**. Something else on your machine owns the port. Either stop the offender, or change Septum's binding: `docker compose up -d` with `-p 3001:3000` in a compose override, or edit `docker-compose.yml`'s `ports:` entry.

**Document ingestion fails with `disk I/O error` under parallel uploads**. SQLite's WAL has a single writer lock; Septum's pool is sized to 50 connections but heavy parallel OCR + Whisper workloads can still saturate. Move to PostgreSQL (compose variant already does this by default) or reduce the ingestion concurrency cap in Settings → Ingestion.

**Whisper model download times out on first audio upload**. The download happens lazily and can take several minutes for larger models. Pre-warm the model from the Settings → Ingestion tab before the first real upload, or run `docker compose exec api python -c "import whisper; whisper.load_model('base')"`.

**Docker Desktop says "insufficient resources"**. Raise the VM memory in Docker Desktop → Settings → Resources to at least 8 GB. Ollama + Presidio + Whisper together are RAM-heavy.

**Something else**. Check the relevant log stream:

```bash
docker compose logs -f api       # backend, pipeline, auth, audit
docker compose logs -f web       # Next.js build + runtime
docker compose logs -f ollama    # model downloads, GPU detection
docker compose logs -f postgres  # database init / migrations
```

If the cause isn't obvious, open a [GitHub issue](https://github.com/byerlikaya/Septum/issues) with the failing log excerpt, your compose variant, and the OS / Docker versions.

---

## Uninstalling

The cleanest removal:

```bash
docker compose down -v           # stops containers + removes named volumes
docker rmi $(docker images 'byerlikaya/septum*' -q)
```

`-v` is the important flag — without it, volumes persist and a subsequent `docker compose up` resurrects your old data. If you want to keep the data "in case", omit `-v` and back the volumes up first (see [Data persistence](#data-persistence--volumes)).

For the standalone image:

```bash
docker stop septum && docker rm septum
docker volume rm septum-data septum-uploads septum-anon-maps \
                 septum-vector-indexes septum-bm25-indexes septum-models
docker rmi byerlikaya/septum
```

For the native install, remove the cloned directory and the Python / npm caches Septum wrote under `~/.cache/septum` (if any).

---

<p align="center">
  <a href="../readme.md"><strong>🏠 Home</strong></a>
  &nbsp;·&nbsp;
  <strong>🚀 Installation</strong>
  &nbsp;·&nbsp;
  <a href="benchmark.md"><strong>📈 Benchmark</strong></a>
  &nbsp;·&nbsp;
  <a href="features.md"><strong>✨ Features</strong></a>
  &nbsp;·&nbsp;
  <a href="architecture.md"><strong>🏗️ Architecture</strong></a>
  &nbsp;·&nbsp;
  <a href="document-ingestion.md"><strong>📊 Document Ingestion</strong></a>
  &nbsp;·&nbsp;
  <a href="screenshots.md"><strong>📸 Screenshots</strong></a>
</p>
