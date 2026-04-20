<p align="center">
  <img src="assets/septum_logo.png" alt="Septum logo" width="220" />
</p>

<h3 align="center">Your data never leaves. Your AI still works.</h3>

<p align="center">
  <a href="https://github.com/byerlikaya/Septum/actions/workflows/tests.yml">
    <img src="https://img.shields.io/github/actions/workflow/status/byerlikaya/Septum/tests.yml?branch=main&label=tests" alt="CI Tests" />
  </a>
  <a href="https://hub.docker.com/r/byerlikaya/septum">
    <img src="https://img.shields.io/docker/v/byerlikaya/septum?label=docker%20image&color=blue&sort=semver" alt="Docker Image Version" />
  </a>
  <a href="https://hub.docker.com/r/byerlikaya/septum">
    <img src="https://img.shields.io/docker/pulls/byerlikaya/septum?label=docker%20pulls" alt="Docker Pulls" />
  </a>
  <a href="https://github.com/byerlikaya/Septum/stargazers">
    <img src="https://img.shields.io/github/stars/byerlikaya/Septum?color=yellow&label=stars" alt="GitHub Stars" />
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/github/license/byerlikaya/Septum?color=blue" alt="License: MIT" />
  </a>
  <a href="README.tr.md">
    <img src="https://img.shields.io/badge/lang-TR-red" alt="Turkish README" />
  </a>
</p>

<p align="center">
  <a href="#how-it-works"><strong>How It Works</strong></a>
  &middot;
  <a href="docs/SCREENSHOTS.md"><strong>Screenshots</strong></a>
  &middot;
  <a href="#quick-start"><strong>Quick Start</strong></a>
  &middot;
  <a href="docs/FEATURES.md"><strong>Features</strong></a>
  &middot;
  <a href="docs/ARCHITECTURE.md"><strong>Architecture</strong></a>
  &middot;
  <a href="CHANGELOG.md"><strong>Changelog</strong></a>
</p>

---

## What is Septum?

Septum is a **privacy-first AI middleware** that sits between you and cloud LLMs. You can ask questions with sensitive company data — and chat freely — with ChatGPT, Claude, Gemini, or any other LLM; Septum detects and masks personal information locally **before it reaches the cloud**.



> **In one sentence:** Septum is a safety layer for teams who want LLM power without leaking personal data — whether it is in a document or in something you typed.

**Before and after — what the LLM actually sees:**

```
Document chunk: "Ahmet Yılmaz was born in Istanbul in 1985. His mother is Ayşe and his father is Ali."
Masked:         "[PERSON_1] was born in [LOCATION_1] in 1985. His mother is [PERSON_2] and his father is [PERSON_3]."

User question:  "Where was Ahmet Yılmaz (mother Ayşe, father Ali) born?"
Masked:         "Where was [PERSON_3] (mother [PERSON_1], father [PERSON_2]) born?"
```

The LLM answers using placeholders. Septum restores real values locally before showing you the response.

---

## How It Works?

```mermaid
sequenceDiagram
    participant U as 👤 User
    participant S as 🛡️ Septum
    participant L as ☁️ Cloud LLM
    U->>S: Where was Ahmet Yılmaz <br> (mother Ayşe, father Ali) born?
    Note over S: Detect & mask PII<br/>3 × PERSON_NAME
    S->>L: Where was [PERSON_3] <br> (mother [PERSON_1], father [PERSON_2]) born?
    L->>S: [PERSON_3] was born in Istanbul.
    Note over S: Placeholders restored locally
    S->>U: Ahmet Yılmaz was born in Istanbul.
    Note over U,L: 🔒 Raw PII never left the machine
```

1. **Upload your documents** — PDFs, Office files, images, audio. Septum detects file type, language, and personal data; masks all PII; prepares anonymised content for search.
2. **Ask questions in chat** — select specific documents, or leave the selection empty and let Septum decide. With no selection, a local Ollama classifier routes the question to either Auto-RAG (search all indexed documents) or a plain chatbot reply.
3. **Your question is masked too** — the same three-layer pipeline runs on the message you typed, not just the documents. Names, phones, emails, IDs in your prompt all turn into placeholders before retrieval.
4. **Approve before sending** — see the masked question, the retrieved chunks, and the assembled cloud prompt side by side. Approve or reject.
5. **Answer with real values** — placeholders are restored locally so you see a natural, human-readable answer.

---

## Architecture

Septum is composed of 7 independent modules split across three security zones. Air-gapped modules handle raw PII with zero internet access. The bridge transports only masked placeholders. Internet-facing modules never see raw PII.

<p align="center">
  <img src="assets/architecture.svg" alt="Septum architecture — 7 modules across 3 security zones (air-gapped, bridge, internet-facing)" width="800" />
</p>

| Package | Zone | Purpose |
|:---|:---|:---|
| [`septum-core`](packages/core/) | Air-gapped | PII detection, masking, unmasking, regulation engine |
| [`septum-mcp`](packages/mcp/) | Air-gapped | MCP server for Claude Desktop, ChatGPT, Cursor |
| [`septum-api`](packages/api/) | Air-gapped | FastAPI REST layer + models, services, auth |
| [`septum-web`](packages/web/) | Air-gapped | Next.js 16 dashboard |
| [`septum-queue`](packages/queue/) | Gateway | Cross-zone broker (file / Redis Streams) |
| [`septum-gateway`](packages/gateway/) | Internet-facing | Cloud LLM forwarder — never imports `septum-core` |
| [`septum-audit`](packages/audit/) | Internet-facing | Compliance log + SIEM export — never imports `septum-core` |

Module contracts and zone semantics live in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Key Features

- **Local PII Protection** — three-layer detection (Presidio + NER + optional Ollama) on both uploaded documents **and** typed chat messages. Documents stored encrypted (AES-256-GCM).
- **Approval Gate** — review the masked prompt, retrieved chunks, and assembled cloud request before any LLM call. Nothing is sent without your review.
- **17 Regulation Packs** — GDPR, KVKK, CCPA, HIPAA, LGPD, PIPEDA, PDPA, APPI, PIPL, POPIA, DPDP, UK GDPR, and more. Multiple active simultaneously; most restrictive wins. Region-specific national ID validators (TCKN checksum, Aadhaar Verhoeff, NRIC/FIN, CPF, NINO, CNPJ, My Number, and more).
- **Auto-RAG Routing** — when no documents are selected, a local Ollama classifier routes between Auto-RAG (search all indexed documents) and a plain chatbot reply. No manual selection required.
- **Custom Rules** — define your own detectors: regex, keyword lists, or LLM-prompt based.
- **Rich Format Support** — PDFs, Office files, spreadsheets, images (OCR), audio (Whisper), emails.
- **Hybrid Retrieval** — BM25 keyword matching + FAISS semantic search with Reciprocal Rank Fusion.
- **Multi-Provider** — Anthropic, OpenAI, OpenRouter, or local Ollama. Switch from the UI.
- **JWT Auth + RBAC + API Keys** — first user auto-promoted via setup wizard; admin UI manages roles (admin / editor / viewer). Programmatic API keys with SHA-256 hashed storage and per-prefix rate limits.
- **MCP Server** — standalone `septum-mcp` exposes the same local masking pipeline to any MCP-aware client (Claude Desktop, ChatGPT Desktop, Cursor, Windsurf, and custom SDK clients).
- **Audit Trail** — append-only compliance log with entity detection metrics. No raw PII in audit events.

See [docs/FEATURES.md](docs/FEATURES.md) for the full detection benchmark, regulation pack table, MCP integration walkthrough, REST API + authentication reference, and the "why Septum" comparison. For every Septum screen — setup wizard, approval gate, document preview, settings tabs, custom regulation rules, audit trail — see [docs/SCREENSHOTS.md](docs/SCREENSHOTS.md).

---

## Quick Start

### Docker (recommended)

```bash
docker pull byerlikaya/septum
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

Open **http://localhost:3000** — the setup wizard walks you through database, cache, LLM provider, regulations, and the first admin account.

**Updating.** Stop and remove the container, run `docker pull byerlikaya/septum`, then re-run the same `docker run` command. Your data is preserved in the volumes.

**Docker Compose.** `docker compose up` starts PostgreSQL, Redis, Ollama, and Septum together. Pull a model before the first chat: `docker compose exec ollama ollama pull llama3.2:3b`. Skip Ollama with `docker compose -f docker-compose.yml -f docker-compose.no-ollama.yml up` for cloud-only setups.

**Deployment topologies** — four compose variants ship for different deployment shapes: standalone (single container, SQLite), full dev stack (all modules on one host), air-gapped zone only, and internet-facing zone only. See [docs/ARCHITECTURE.md § Deployment Topologies](docs/ARCHITECTURE.md#deployment-topologies) for the full matrix and a two-host air-gap walkthrough.

### Local development

```bash
./dev.sh --setup   # first time: install deps
./dev.sh           # start dev servers (port 3000)
```

The setup wizard opens on first visit.

### Docker vs Local

All features work identically. The difference is acceleration: local install picks up whatever torch variant PyPI serves for your host (CUDA on NVIDIA Linux, MPS on Apple Silicon), while the published Docker image is CPU-only (a separate `byerlikaya/septum:gpu` variant ships with full CUDA runtime for NVIDIA Linux hosts). CPU inference handles typical workloads; GPU matters only for batch OCR or audio transcription at scale.

---

## Learn More

- **[docs/FEATURES.md](docs/FEATURES.md)** — detection benchmark, regulation packs, MCP deep-dive, REST API + auth, why-Septum comparison
- **[docs/SCREENSHOTS.md](docs/SCREENSHOTS.md)** — visual tour of every screen
- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — module contracts, zone semantics, deployment topologies, API reference
- **[CHANGELOG.md](CHANGELOG.md)** — date-based release history

---

## Support the Project

Septum is open source (MIT) and maintained in the open. If it saves you from a privacy incident, helps your team ship faster, or just makes your LLM workflow safer:

- ⭐ **Star the repo on [GitHub](https://github.com/byerlikaya/Septum)** — the biggest signal that this project is worth continued investment.
- **Open issues and discussions** for bugs or features you need — every report shapes the roadmap.
- **Tell your team** — privacy-first AI tooling is still rare, and word of mouth matters more than any ad.


---

## License

See [LICENSE](LICENSE) for details.
