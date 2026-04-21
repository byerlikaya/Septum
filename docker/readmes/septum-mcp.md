<p align="center">
  <img src="https://raw.githubusercontent.com/byerlikaya/Septum/main/assets/septum_logo.png" alt="Septum logo" width="180" />
</p>

<h3 align="center">Septum MCP — Model Context Protocol server (air-gapped zone)</h3>

<p align="center">
  <img src="https://img.shields.io/badge/zone-air--gapped-1E88E5" alt="Air-gapped zone" />
  <a href="https://modelcontextprotocol.io"><img src="https://img.shields.io/badge/protocol-MCP-6E56CF" alt="MCP" /></a>
  <a href="https://github.com/byerlikaya/Septum"><img src="https://img.shields.io/badge/GitHub-Source-181717?logo=github" alt="GitHub" /></a>
  <a href="https://github.com/byerlikaya/Septum/blob/main/packages/mcp/README.md"><img src="https://img.shields.io/badge/docs-MCP%20guide-blue" alt="MCP guide" /></a>
</p>

---

Stdio / streamable-http / sse MCP server exposing Septum's local PII masking pipeline to any MCP-aware client — Claude Code, Claude Desktop, Cursor, Windsurf, Zed, ChatGPT Desktop. Bundles `septum-core` so detection runs in-process; raw PII never touches the network.

## Transports

| Transport | Use when |
|---|---|
| `stdio` (default) | Local clients that launch the server as a subprocess |
| `streamable-http` | Remote agents, browser extensions, shared team servers — bearer-token gated |
| `sse` | Legacy clients that haven't migrated to streamable-http |

## Quick start (streamable-http)

```bash
docker run --name septum-mcp -p 8765:8765 \
  -e SEPTUM_MCP_HTTP_TOKEN=$(openssl rand -hex 32) \
  -e SEPTUM_REGULATIONS=gdpr,kvkk \
  byerlikaya/septum-mcp
```

Point your MCP client at `http://localhost:8765/mcp` with `Authorization: Bearer <token>`.

## Tools exposed

| Tool | Purpose |
|---|---|
| `mask_text` | Mask PII in a snippet and return a session id |
| `unmask_response` | Restore originals in an LLM reply using the session id |
| `detect_pii` | Read-only scan — returns entities without retaining a session |
| `scan_file` | Read a local file (txt / md / csv / json / pdf / docx) and scan it |
| `list_regulations` | List the 17 built-in regulation packs |
| `get_session_map` | Return `{original → placeholder}` for local debugging |

## Tags

| Tag | Contents |
|---|---|
| `latest`, `1`, `1.0`, `1.0.0` | Multi-arch (amd64 + arm64), CPU-only PyTorch |

## Links

- **Source:** [github.com/byerlikaya/Septum](https://github.com/byerlikaya/Septum)
- **MCP server guide:** [packages/mcp/README.md](https://github.com/byerlikaya/Septum/blob/main/packages/mcp/README.md)
- **MCP spec:** [modelcontextprotocol.io](https://modelcontextprotocol.io)
