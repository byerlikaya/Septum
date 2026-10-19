# Septum — Installation Guide

<p align="center">
  <a href="./index.md"><strong>🏠 Home</strong></a>
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
docker compose up --build
```

Open **http://localhost:3000** — the setup wizard takes you the rest of the way.

The compose stack brings up PostgreSQL, Redis, Ollama, the FastAPI backend, and the Next.js dashboard on a single host. Septum is installed from source: the first run builds the Septum images locally (several minutes) and pulls the base services and a default Ollama model; subsequent runs reuse the build cache. No raw PII ever leaves the machine — detection, masking, and LLM inference all happen locally.

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

- **Apple Silicon (M1/M2/M3/M4)** — fully supported. Ollama uses Metal acceleration automatically; no extra flags needed. The images build natively for arm64.
- **NVIDIA GPU (Linux amd64)** — optional. Build the `docker/standalone.Dockerfile` or `docker/api.Dockerfile` image with the build argument `TORCH_VARIANT=gpu` (add `TORCH_VARIANT: gpu` under the service's `build.args` in the compose file and run `up --build`; or build it yourself with `docker build -f docker/api.Dockerfile --build-arg TORCH_VARIANT=gpu -t septum-api:local .` and start the stack without `--build` so compose keeps that tag) for CUDA-accelerated PyTorch (faster OCR, Whisper, embedding). The host needs an NVIDIA driver with CUDA 13 support. Ollama runs on the GPU automatically when one is visible inside the container.
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

docker compose up --build       # foreground — watch the first-boot logs
# or
docker compose up -d --build    # detached — release the terminal

# First-time: pull a default model into the bundled Ollama
docker compose exec ollama ollama pull llama3.2:3b
```

Navigate to **http://localhost:3000**. See the [First launch](#first-launch--setup-wizard) section for the wizard walkthrough.

The optional `mcp` service (profile `mcp`) mounts the models volume read-only, so it only loads models that are already in the volume and pass the hash check. Warm `api` or run the fetch command first (`docker compose run --rm api python -m septum_api.cli.models fetch`) before relying on `mcp` for languages that `api` has not loaded yet.

To run without Ollama (cloud-provider-only deployments):

```bash
docker compose -f docker-compose.yml -f docker-compose.no-ollama.yml up --build
```

### 2. Standalone single container — **demo**

One container, SQLite, no external services. Fastest way to try Septum on a single laptop; ideal for sales demos and hobby deployments.

```bash
git clone https://github.com/byerlikaya/Septum.git && cd Septum
docker compose -f docker-compose.standalone.yml up -d --build
```

Standalone does not bundle Ollama. Either run Ollama on the host (`brew install ollama && ollama serve`) and point the wizard at `http://host.docker.internal:11434`, or skip Ollama and use a cloud provider (Anthropic / OpenAI / OpenRouter).

**Use standalone when**: you want the fastest try-it-out experience, your corpus is small, and you don't need the full modular zone separation.

**Don't use standalone when**: you need air-gap separation, multi-user scale, or the full Septum feature set (semantic PII layer + Auto-RAG routing both want Ollama).

### 3. Air-gapped zone only

Runs only the air-gapped zone (api + web + PostgreSQL + Redis) on a host with no internet egress. Cloud LLM calls travel via a separate internet-facing host that hosts the gateway.

```bash
cp .env.example .env
# Set POSTGRES_PASSWORD + REDIS_PASSWORD

docker compose -f docker-compose.airgap.yml up -d --build
```

This topology sets `USE_GATEWAY_DEFAULT=true` so every LLM call is routed through `septum-queue` (Redis Streams) instead of HTTP. Only masked text crosses the zone boundary. Pair with variant 4 on the internet-facing host.

**Fetching the models for an offline host.** The air-gapped host has no internet access, so its model cache is filled on a connected machine and copied over. The pins live in the code, so both machines must run the same Septum checkout. On the connected machine, with the same `.env`:

```bash
docker compose -f docker-compose.airgap.yml build api
docker compose -f docker-compose.airgap.yml run --rm -e HF_HUB_OFFLINE=0 api python -m septum_api.cli.models fetch --whisper base
docker run --rm -v septum_septum-models:/src -v "$PWD":/dst alpine tar czf /dst/septum-models.tar.gz -C /src .
```

The `fetch` command downloads and verifies every default model at its pinned commit, then the Whisper model named by `--whisper` (repeat the option for each size you use). Without `--whisper` it fetches the `WHISPER_MODEL` setting, which defaults to `base`. The last command archives the models volume; the volume name carries the Compose project prefix (`septum_` for a checkout in a directory named `Septum`). The air-gapped compose file sets `HF_HUB_OFFLINE=1`, so the fetch commands override it with `-e HF_HUB_OFFLINE=0`; this is the only step that downloads, and it runs on the connected host. Copy `septum-models.tar.gz` to the air-gapped host, then:

```bash
docker compose -f docker-compose.airgap.yml build api
docker compose -f docker-compose.airgap.yml create
docker run --rm -v septum_septum-models:/dst -v "$PWD":/src alpine tar xzf /src/septum-models.tar.gz -C /dst
docker compose -f docker-compose.airgap.yml up -d
docker compose -f docker-compose.airgap.yml run --rm api python -m septum_api.cli.models fetch --whisper base --offline-check
```

`--offline-check` verifies the copied files without touching the network. It prints one line per model and exits with status 1 if any file is missing or does not match its pin.

Both hosts need the `alpine` image locally: pull it on the connected machine and transfer it with `docker save` and `docker load`. Any image that contains `tar` can replace it in the commands above. The air-gapped compose file sets `HF_HUB_OFFLINE=1` on the `api` and `mcp` services, so those containers do not download models. The `mcp` service, when its profile is enabled, mounts the models volume read-only, so it only loads models that are already in the volume and pass the hash check. Pre-populate the volume with the `fetch` command (all default pins) before relying on `mcp` for languages that `api` has not loaded yet.

**Custom NER models.** A custom model is fetched on the connected host with `--user-pin`, using the same `.env`. As above, `-e HF_HUB_OFFLINE=0` overrides the offline setting for this download. Repeat the option for each custom model:

```bash
docker compose -f docker-compose.airgap.yml run --rm -e HF_HUB_OFFLINE=0 api python -m septum_api.cli.models fetch --user-pin org/name@<40-character commit SHA>
docker run --rm -v septum_septum-data:/src -v "$PWD":/dst alpine cp /src/septum-user-pins.json /dst/
```

The user-pins file is `septum-user-pins.json` next to `config.json`, so it lives in the `septum-data` volume and not in the models volume. Copy it to the air-gapped host with the models archive. After the `create` step described above, and before `up -d`, restore it into the data volume:

```bash
docker run --rm -v septum_septum-data:/dst -v "$PWD":/src alpine cp /src/septum-user-pins.json /dst/
```

### 4. Internet-facing zone only

Runs only the gateway + audit modules on a host that *can* reach the cloud LLM providers. Consumes masked requests from Redis and forwards them to Anthropic / OpenAI / OpenRouter.

```bash
cp .env.example .env
# Set REDIS_PASSWORD (same value as the air-gapped host, over VPN / private net)
# Set provider keys: ANTHROPIC_API_KEY, OPENAI_API_KEY, etc.

docker compose -f docker-compose.gateway.yml up -d --build
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

Requirements: Python 3.11+ (3.13 tested), Node.js 22+ (the Docker images and CI build with Node 26), ffmpeg (for Whisper audio ingestion). The bootstrap script writes `config.json` with a freshly generated encryption key and JWT secret on first run; override with `SEPTUM_CONFIG_PATH`. Set `SEPTUM_DISABLE_MODEL_WARMUP=1` to skip the background model preload at startup; by default the NER, spaCy and embedding models are preloaded. With warmup disabled, the startup warning about stored model settings is skipped as well. `SEPTUM_USER_PINS_PATH` sets where the hashes of custom NER models are recorded; by default it is `septum-user-pins.json` next to `config.json` (`/app/data` in Docker). The `mcp` service does not mount the app data volume, because that volume holds `config.json` and the encryption key. So `mcp` cannot read a user-pins file from there and cannot use custom user-pinned NER models out of the box. To use one in `mcp`, give it a copy of the user-pins file in a read-only mount and point `SEPTUM_USER_PINS_PATH` at that copy.

**Bumping the model pins (maintainers).** `python packages/api/scripts/pin_models.py --write-manifest` reads the `main` commit of every default model from the Hugging Face Hub and regenerates `packages/core/septum_core/model_pins.py`; review the diff before committing it. `--check` re-reads each pinned commit and exits with status 1 on any drift. Both need network access.

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

### Exact values to paste (full compose stack)

If you started with `docker compose up` (the full stack), the api container reaches the bundled PostgreSQL, Redis, and Ollama over the compose network — not over `localhost`. Use the compose service names as hostnames:

| Step | Field | Paste this (replace `<…>` with your `.env` value) |
|---|---|---|
| **1. Database → PostgreSQL** | URL | `postgresql+asyncpg://septum:<POSTGRES_PASSWORD>@postgres:5432/septum` |
| **2. Cache → Redis** | URL | `redis://:<REDIS_PASSWORD>@redis:6379/0` |
| **3. LLM provider → Ollama (bundled)** | URL | `http://ollama:11434` |

`<POSTGRES_PASSWORD>` / `<REDIS_PASSWORD>` are the values you set in `.env` at quickstart. Recover them with `grep -E 'POSTGRES_PASSWORD\|REDIS_PASSWORD' .env`. Note that the Redis URL has an empty user name between `://` and `:` — Redis's implicit `default` user; the colon-prefix before the password is required.

**Bringing your own Redis?** Run it with `maxmemory-policy noeviction` (the Redis default, which the bundled compose Redis uses). Erasing a document leaves a short-lived tombstone in Redis that stops other workers from serving a cached copy of its anonymization map; any eviction policy — `allkeys-*` or `volatile-*`, since the tombstone carries a TTL — could drop it early.

**Already have Ollama running on the host?** Stop the bundled one (`docker compose stop ollama && docker compose rm -f ollama`) so the port does not conflict, then use `http://host.docker.internal:11434` as the Ollama URL — docker-compose.yml wires `host.docker.internal` into the api container via `extra_hosts`, so it resolves back to your host's Ollama without any extra setup.

**Ollama URL rule.** Septum accepts an Ollama URL only when its host is loopback (`127.0.0.0/8`, `::1`), `localhost`, `host.docker.internal`, `host.containers.internal`, or `ollama` (the bundled compose service). Any other host is refused with HTTP 400 (`ollama_url_not_allowed`), including LAN or private IP addresses, `.local` names, and remote servers, even over `https://`, because Ollama receives unmasked text and raw PII must never leave the machine. `OLLAMA_BASE_URL` is copied into settings at first setup and used whenever no URL is saved; after that the saved URL governs. The same rule covers that variable: a refused value is not replaced by the default, so Ollama calls are refused until it is corrected. A custom compose service name for Ollama is not accepted. The names `ollama`, `host.docker.internal` and `host.containers.internal` are trusted by name, so do not alias them to another machine in `/etc/hosts`, DNS or container networking; Septum checks the name, not where it resolves. If a previously saved URL is now refused, Ollama features stay off, and the settings page flags it under the URL field until it is changed.

Each step has a **Test connection** button — it runs inside the api container, so it reflects what the backend will see after you finish the wizard. Green check = safe to move on.

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

The compose files declare named Docker volumes so your data survives container restarts and rebuilds. The standalone file uses the fixed names below; the full stack (`docker-compose.yml`) and the air-gapped file prefix them with the compose project name (for example `septum_septum-data`).

| Volume | Path inside container | Content |
|---|---|---|
| `septum-data` | `/app/data` | SQLite DB (if used), `config.json`, encryption keys |
| `septum-uploads` | `/app/uploads` | Uploaded original files, AES-256-GCM encrypted |
| `septum-anon-maps` | `/app/anon_maps` | Per-document PII placeholder mappings, encrypted |
| `septum-vector-indexes` | `/app/vector_indexes` | FAISS embeddings |
| `septum-bm25-indexes` | `/app/bm25_indexes` | BM25 keyword indexes |
| `septum-models` | `/app/models` | Cached HuggingFace, Whisper and PaddleOCR models |
| `septum-pgdata` | `/var/lib/postgresql/data` | PostgreSQL cluster (compose only) |
| `septum-redis-data` | `/data` | Redis AOF/RDB (compose only) |
| `ollama-data` | `/root/.ollama` | Downloaded Ollama models (full stack only) |
| `septum-audit-log` | `/var/log/septum` | Audit log files (full stack and gateway file) |
| `septum-mcp-scan` | `/data/scan` | MCP scan directory, mounted read-only (full stack and air-gapped file) |

**Backup.** A consistent backup means two things: a PostgreSQL dump *and* the encrypted volumes (uploads + anon-maps + vector/BM25 indexes). The encryption key lives in `septum-data/config.json` — lose it and every encrypted volume becomes unreadable.

```bash
# Dump Postgres (full stack only)
docker compose exec -T postgres pg_dump -U septum septum > septum-$(date +%F).sql

# Tar the named volumes (works for standalone and the full stack)
for v in data uploads anon-maps vector-indexes bm25-indexes models; do
  name=$(docker volume ls -q --filter "label=com.docker.compose.volume=septum-$v" | head -n 1)
  name=${name:-septum-$v}
  docker volume inspect "$name" >/dev/null 2>&1 || { echo "volume $name not found, skipped" >&2; continue; }
  docker run --rm -v "$name":/src -v "$PWD":/dst alpine \
    tar czf /dst/septum-$v-$(date +%F).tar.gz -C /src .
done
```

The loop looks up the real volume name from the label compose puts on it, so the project prefix does not matter. Run it with only one Septum stack on the host.

**Restore** is the reverse. Stop the stack, `docker volume rm` the existing volumes, recreate + extract, start.

---

## Upgrading

Septum follows semver. Minor versions never break configs; major versions are called out in the [Changelog](../changelog.md) with explicit migration notes.

```bash
git pull                                    # compose file changes occasionally
docker compose up -d --build                # rebuild the images and recreate the containers
docker compose logs -f api                  # watch the migration run
```

Alembic migrations apply automatically at api boot. If a migration fails, the api container exits with a descriptive error — inspect the logs, resolve the cause, retry the `up -d --build`.

For the standalone variant, run the same command with its compose file:

```bash
git pull
docker compose -f docker-compose.standalone.yml up -d --build
```

Volumes are preserved across the rebuild because they are named volumes.

**Model caches after an upgrade.** Model caches from an earlier version may not match the pinned revisions. On the next online start, a pinned snapshot that holds leftover unpinned files is purged and fetched once; offline, it is refused until it is fetched again. An air-gapped install must run the `fetch` command again on a connected host and copy the models volume and the user-pins file, as described in [variant 3](#3-air-gapped-zone-only). A stored bare custom NER id is refused with `ner_model_unpinned`. A stored Whisper model name outside the openai-whisper registry (such as a local checkpoint path) is refused with `whisper_model_not_allowed`. Both must be changed in Settings. Unless warmup is disabled, the startup log then warns, with error codes only, about each stored custom NER model or Whisper model that would be refused.

**Coming from the published `byerlikaya/septum` image.** Septum no longer publishes container images; the older images on Docker Hub remain available but are not updated. Remove the old container only, then build and start the standalone compose file. The container removal keeps the volumes, and the compose file uses the same volume names (`septum-data`, `septum-uploads`, `septum-anon-maps`, `septum-vector-indexes`, `septum-bm25-indexes`, `septum-models`), so your data, `config.json` and encryption key are reused.

```bash
docker rm -f septum
git clone https://github.com/byerlikaya/Septum.git && cd Septum
docker compose -f docker-compose.standalone.yml up -d --build
```

If you already ran the standalone compose file before it used fixed volume names, your data sits in prefixed volumes (for example `septum_septum-data`). Copy each one once into the fixed-name volume, repeating for every volume in the table above:

```bash
docker run --rm -v septum_septum-data:/from -v septum-data:/to alpine cp -a /from/. /to/
```

Never run `docker volume prune` while moving: it deletes every volume no container currently uses, including the ones that hold your data.

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
```

`-v` is the important flag — without it, volumes persist and a subsequent `docker compose up` resurrects your old data. If you want to keep the data "in case", omit `-v` and back the volumes up first (see [Data persistence](#data-persistence--volumes)).

For the standalone variant:

```bash
docker compose -f docker-compose.standalone.yml down -v
```

For the native install, remove the cloned directory and the Python / npm caches Septum wrote under `~/.cache/septum` (if any).

---

<p align="center">
  <a href="./index.md"><strong>🏠 Home</strong></a>
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
