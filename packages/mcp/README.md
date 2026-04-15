# septum-mcp

Model Context Protocol (MCP) server that exposes
[Septum](https://github.com/byerlikaya/Septum)'s privacy-first PII
masking pipeline to **any MCP-compatible client**. MCP is an open,
vendor-neutral protocol (see the
[specification](https://modelcontextprotocol.io)) — the server is
written against the official Python SDK, speaks the standard stdio
transport, and makes no assumptions about which client connects to
it. Known working clients include Claude Code, Claude Desktop,
Cursor, Zed, Cline, Continue, Windsurf, the LangChain /
LlamaIndex MCP adapters, and any custom client built with the
Python / TypeScript / Rust / Go / C# / Java SDKs.

All detection runs **locally** via `septum-core`. Raw PII never
leaves the machine. The server is transport-only: it wraps the
core engine and hands the caller six tools.

## Tools

| Tool | Description |
|---|---|
| `mask_text` | Detect and mask PII in text. Returns masked text + `session_id`. |
| `unmask_response` | Restore original PII values in an LLM response using `session_id`. |
| `detect_pii` | Scan text for PII without retaining a session. |
| `scan_file` | Read a local file (`.txt`, `.md`, `.csv`, `.json`, `.pdf`, `.docx`) and scan its content. |
| `list_regulations` | List built-in regulation packs with their declared entity types. |
| `get_session_map` | Return the `{original → placeholder}` map for a session (debugging only). |

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

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `SEPTUM_REGULATIONS` | `gdpr` | Comma-separated regulation pack ids (e.g. `gdpr,kvkk,hipaa`). |
| `SEPTUM_LANGUAGE` | `en` | Default ISO 639-1 language hint when a tool call omits one. |
| `SEPTUM_USE_NER` | `true` | Enable the transformer NER layer. Set to `false` to avoid downloading models. |
| `SEPTUM_USE_PRESIDIO` | `true` | Enable the Presidio recognizer layer. |
| `SEPTUM_SESSION_TTL` | `3600` | Seconds before an idle anonymization session is evicted. `0` disables eviction. |

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
  the stdio process and are dropped when the server exits or the
  TTL elapses. Nothing is persisted to disk.
- **`get_session_map` returns raw PII.** It is meant for local
  debugging tools. Do not forward its output to a remote system.

## Running the test suite

```bash
pip install -e 'packages/core[transformers]'
pip install -e 'packages/mcp[test]'
pytest packages/mcp/tests/
```

The suite covers config parsing, file readers, all six tool
implementations, and one stdio end-to-end smoke test that runs the
real server as a subprocess.
