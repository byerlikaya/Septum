# septum-mcp

> 🇹🇷 [Türkçe sürüm](README.tr.md)

Model Context Protocol (MCP) server that exposes
[Septum](https://github.com/byerlikaya/Septum)'s privacy-first PII
masking pipeline to **any MCP-compatible client**. MCP is an open,
vendor-neutral protocol (see the
[specification](https://modelcontextprotocol.io)) — the server is
written against the official Python SDK and supports all three
standard transports:

- **stdio** (default) — for local clients that spawn the server as a
  subprocess: Claude Code, Claude Desktop, Cursor, Windsurf, Zed,
  Cline, Continue, and the LangChain / LlamaIndex MCP adapters.
- **streamable-http** — modern HTTP transport for remote, containerised,
  or browser-based clients. Secured by a static bearer token.
- **sse** — legacy HTTP + Server-Sent Events transport, kept for
  clients that have not migrated to streamable-http yet.

All detection runs **locally** via `septum-core`. Raw PII never
leaves the machine. The server is transport-only: it wraps the
core engine and hands the caller five tools (plus one opt-in
debugging tool, `get_session_map`).

## Tools

| Tool | Description |
|---|---|
| `mask_text` | Detect and mask PII in text. Returns masked text + `session_id`. |
| `unmask_response` | Restore original PII values in an LLM response using `session_id`. Only placeholders that session emitted are restored, up to `SEPTUM_MCP_MAX_UNMASK_PLACEHOLDERS` distinct placeholders per session. |
| `detect_pii` | Scan text for PII without retaining a session. |
| `scan_file` | Read a local file (`.txt`, `.md`, `.csv`, `.json`, `.pdf`, `.docx`) and scan its content. Over HTTP/SSE it requires `SEPTUM_MCP_FILE_ROOT`. |
| `list_regulations` | List built-in regulation packs with their declared entity types. |
| `get_session_map` | **Opt-in, stdio only, off by default.** Return the `{original → placeholder}` map for a session (debugging only). See `SEPTUM_MCP_EXPOSE_SESSION_MAP`. |

## Installation

```bash
pip install septum-mcp
```

Or from source while the package is still on the
`refactor/modular-architecture` branch:

```bash
pip install -e packages/core
pip install -e packages/mcp
```

## Client configuration

Any MCP client exposes a way to register a server — the block below
is the canonical `mcpServers` JSON shape used by Claude Code
(`~/.claude/mcp.json`), Claude Desktop
(`~/Library/Application Support/Claude/claude_desktop_config.json`
on macOS), Cursor (Settings → MCP), and most other clients. Editors
that use a different config file (Zed, Cline, Continue, …) ship
their own UI for the same fields: the `command`, `args`, and `env`
entries are all you need from this document.

### Method A — installed via pip (recommended)

```json
{
  "mcpServers": {
    "septum": {
      "command": "septum-mcp",
      "env": {
        "SEPTUM_REGULATIONS": "gdpr,kvkk",
        "SEPTUM_LANGUAGE": "en",
        "SEPTUM_USE_NER": "true"
      }
    }
  }
}
```

### Method B — uvx (isolated env, no pip install needed)

```json
{
  "mcpServers": {
    "septum": {
      "command": "uvx",
      "args": ["septum-mcp"],
      "env": {
        "SEPTUM_REGULATIONS": "gdpr",
        "SEPTUM_USE_NER": "false"
      }
    }
  }
}
```

### Method C — local dev from repo

```json
{
  "mcpServers": {
    "septum": {
      "command": "python",
      "args": ["-m", "septum_mcp.server"],
      "env": {
        "SEPTUM_REGULATIONS": "gdpr",
        "PYTHONPATH": "/absolute/path/to/Septum/packages/core:/absolute/path/to/Septum/packages/mcp"
      }
    }
  }
}
```

## Remote HTTP deployment

Use HTTP transport when the MCP client cannot spawn a subprocess:
web-based agents, browser extensions, a shared team server, or a
container-orchestrator deployment. The server exposes the same five
tools; only the transport changes. `get_session_map` is never
available over HTTP or SSE.

### Running the server

```bash
# Generate a token once (store it in a secret manager):
openssl rand -hex 32

# Run the server, authenticated
SEPTUM_MCP_HTTP_TOKEN=<random-token> septum-mcp \
  --transport streamable-http \
  --host 0.0.0.0 \
  --port 8765
```

CLI flags (`--transport`, `--host`, `--port`, `--token`,
`--mount-path`) override the corresponding `SEPTUM_MCP_*` env vars.

Without a token the server refuses to enforce auth — that is
explicit (logged on startup) and only intended for localhost.

### Client configuration (streamable-http)

```jsonc
{
  "mcpServers": {
    "septum": {
      "url": "https://mcp.example.com/mcp",
      "headers": {
        "Authorization": "Bearer <your-token>"
      }
    }
  }
}
```

The URL path defaults to `/mcp` for streamable-http and `/sse` for
sse — override with `--mount-path` or `SEPTUM_MCP_HTTP_MOUNT_PATH`.

### Docker

```bash
docker run -p 8765:8765 \
  -e SEPTUM_MCP_HTTP_TOKEN=<random-token> \
  -e SEPTUM_MCP_HTTP_HOST=0.0.0.0 \
  byerlikaya/septum-mcp:latest
```

Or with docker-compose, pick up the `mcp` profile in `docker-compose.yml`:

```bash
SEPTUM_MCP_HTTP_TOKEN=<random-token> \
  docker compose --profile mcp up mcp
```

### Deployment notes

- **Always run behind TLS in production.** The bearer token is
  transmitted as-is in the `Authorization` header; a reverse proxy
  (Caddy, nginx, Traefik) with an automatic Let's Encrypt cert is
  the usual path.
- **`/health`** answers `200 OK` unconditionally (bypasses auth) so
  reverse-proxy probes and Docker `HEALTHCHECK` directives work
  without a token.
- **Single-tenant only today.** All HTTP clients share one
  `SeptumEngine` and therefore one anonymization-session registry.
  Multi-tenant isolation (per-client scoped sessions) is on the
  roadmap — for now, run separate instances per tenant if that
  matters.

## Environment variables

**Core / shared across all transports:**

| Variable | Default | Description |
|---|---|---|
| `SEPTUM_REGULATIONS` | all 17 packs | Comma-separated regulation pack ids (e.g. `gdpr,kvkk,hipaa`). |
| `SEPTUM_LANGUAGE` | `en` | Default ISO 639-1 language hint when a tool call omits one. |
| `SEPTUM_USE_NER` | `true` | Enable the transformer NER layer. Set to `false` to avoid downloading models. |
| `SEPTUM_USE_PRESIDIO` | `true` | Enable the Presidio recognizer layer. |
| `SEPTUM_SESSION_TTL` | `3600` | Seconds before an idle anonymization session is evicted. `0` disables eviction. |
| `SEPTUM_MCP_EXPOSE_SESSION_MAP` | `false` | Set to `true` to register `get_session_map`. Honoured on stdio only. **Returns raw PII to the MCP client** — see the security notes. |
| `SEPTUM_MCP_MAX_UNMASK_PLACEHOLDERS` | `25` | Per-session restore budget: most distinct placeholders `unmask_response` may restore for one session across all its calls. A call that would exceed it fails and nothing is restored; re-restoring an already restored placeholder is free. Positive integer; invalid values fall back to the default. |
| `SEPTUM_MCP_FILE_ROOT` | unset | Directory `scan_file` is confined to. Optional on stdio; on HTTP/SSE `scan_file` refuses every path while unset. It must exist as a directory when the server starts (otherwise startup fails with a clear error) and is pinned then: a root behind a symlink is allowed and fixed to its target, and replacing the root path later refuses reads instead of moving the boundary. The server only reads from it, so mount it read-only (the bundled compose files mount `septum-mcp-scan` at `/data/scan:ro`). |
| `SEPTUM_MCP_MAX_FILE_BYTES` | `52428800` | Largest file `scan_file` will read (50 MB). |

**HTTP transport only (ignored in stdio mode):**

| Variable | Default | Description |
|---|---|---|
| `SEPTUM_MCP_TRANSPORT` | `stdio` | Transport to bind: `stdio`, `streamable-http`, or `sse`. |
| `SEPTUM_MCP_HTTP_HOST` | `127.0.0.1` | Bind address. Set to `0.0.0.0` only behind TLS + auth. |
| `SEPTUM_MCP_HTTP_PORT` | `8765` | TCP port. |
| `SEPTUM_MCP_HTTP_TOKEN` | unset | Bearer token for `Authorization: Bearer <token>`. When unset, HTTP runs without auth (localhost only). |
| `SEPTUM_MCP_HTTP_MOUNT_PATH` | SDK default | URL path prefix (`/mcp` for streamable-http, `/sse` for sse). |

Supported regulation pack ids: `gdpr`, `kvkk`, `ccpa`, `cpra`,
`hipaa`, `pipeda`, `lgpd`, `pdpa_th`, `pdpa_sg`, `appi`, `pipl`,
`popia`, `dpdp`, `uk_gdpr`, `pdpl_sa`, `nzpa`, `australia_pa`.

## Usage examples

### Mask before sending to a remote model

```jsonc
// Tool call
{
  "name": "mask_text",
  "arguments": {
    "text": "Contact Jane Doe at jane@example.com about invoice 1234.",
    "language": "en"
  }
}

// Response
{
  "masked_text": "Contact [PERSON_1] at [EMAIL_ADDRESS_1] about invoice 1234.",
  "session_id": "e8f1...",
  "entity_count": 2,
  "entity_type_counts": { "PERSON": 1, "EMAIL_ADDRESS": 1 }
}
```

### Unmask the LLM reply

```jsonc
{
  "name": "unmask_response",
  "arguments": {
    "text": "I've drafted a follow-up to [PERSON_1] at [EMAIL_ADDRESS_1].",
    "session_id": "e8f1..."
  }
}
```

### Scan a local file

```jsonc
{
  "name": "scan_file",
  "arguments": {
    "file_path": "/Users/alice/notes/client.pdf",
    "mask": true
  }
}
```

## Supported file formats

The `scan_file` tool handles plain text, Markdown, CSV, JSON, PDF
(via `pypdf`), and DOCX (via `python-docx`). OCR (scanned PDFs,
images) and audio transcription are intentionally out of scope for
this package — they require multi-hundred-megabyte models and belong
in the full Septum API/pipeline instead.

## Scope and security notes

- **septum-mcp is air-gapped.** It has no outbound network calls.
  Even the optional NER layer loads models from the local Hugging
  Face cache. If you disable `SEPTUM_USE_NER`, no model downloads
  happen at all.
- **Sessions are in-memory only.** Anonymization maps live inside
  the server process and are dropped when the server exits or the
  per-session TTL elapses. Nothing is persisted to disk.
- **`get_session_map` is off by default because it returns raw PII.**
  The MCP client is usually driven by a cloud model, so anything a
  tool returns can end up in that model's context — the session map
  would hand it every original value behind the placeholders and
  defeat masking. The tool is not registered (it does not appear in
  `tools/list`) unless you set `SEPTUM_MCP_EXPOSE_SESSION_MAP=true`,
  and even then only over stdio; HTTP and SSE ignore the flag and log
  a warning. Enable it only for local debugging with a client that
  does not forward tool output to a remote model, and turn it off
  again afterwards.
- **`unmask_response` is scoped to what the session emitted.** It
  restores only placeholders that appeared in that session's
  `mask_text` or `scan_file` (`mask=true`) output; any other
  placeholder, and every placeholder in a session the tools never
  masked, stays literal. Each session has a restore budget of
  `SEPTUM_MCP_MAX_UNMASK_PLACEHOLDERS` distinct placeholders (default
  `25`) counted across **all** of its calls: a call that would push the
  session past it is refused without restoring anything, so splitting
  a crafted reply into many small calls does not get around it.
  Restoring a placeholder the session already restored costs nothing.
  Within the budget the restored values are still raw PII, so treat
  unmasked output accordingly.
- **`scan_file` is confined.** It refuses symlinks, dotfiles and
  anything under a dot-directory (`.env`, `.ssh/`, `.git/`), and files
  above `SEPTUM_MCP_MAX_FILE_BYTES`. Set `SEPTUM_MCP_FILE_ROOT` to
  confine it to one directory. On stdio the root is optional; on HTTP
  and SSE `scan_file` refuses every path until the root is set, so a
  remote client can never read arbitrary files the server process can.
  The root must exist when the server starts and is pinned to the
  directory it resolves to then; if the root path is later replaced
  (for instance by a symlink), reads are refused rather than following
  it. A path outside the root, a missing file and a dotfile all get the
  same error, which names neither the path nor the root.
- **HTTP mode requires a bearer token** for any non-loopback bind.
  `SEPTUM_MCP_HTTP_TOKEN` gates every request except `/health`; a
  non-loopback host with no token logs a loud startup warning.
  Always terminate TLS in front of the server (reverse proxy) so
  the token is not sent in cleartext.
- **HTTP mode is single-tenant today.** All connected clients share
  one `SeptumEngine` and therefore one anonymization-session
  registry. Run separate instances per tenant if isolation matters.

## Running the test suite

```bash
pip install -e 'packages/core[transformers]'
pip install -e 'packages/mcp[test]'
pytest packages/mcp/tests/
```

The suite covers config parsing, file readers, all six tool
implementations, and one stdio end-to-end smoke test that runs the
real server as a subprocess.
