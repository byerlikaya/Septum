# septum-gateway

> 🇹🇷 [Türkçe sürüm](README.tr.md)

Internet-facing LLM forwarder for [Septum](https://github.com/byerlikaya/Septum). Consumes masked chat requests from `septum-queue` and dispatches them to cloud LLM providers (Anthropic, OpenAI, OpenRouter). Raw PII never enters the gateway process — the air-gapped `septum-api` masks every payload via `septum-core` before it hits the queue.

## Design guarantees

- **Never imports `septum-core`.** The gateway lives in the internet-facing zone and has no reason to look at raw PII. This wall is enforced in code review, not just by deployment — if a future change tries to add `from septum_core import ...` the refactor lint rule blocks it.
- **Stateless.** No database, no session store, no cache. Every request is a pure function: envelope in, envelope out.
- **Provider-agnostic.** Adding a new cloud provider is a single subclass of `_OpenAICompatibleForwarder` (or a from-scratch `BaseForwarder` if the API is not OpenAI-shaped).
- **Optional web server.** Core use (consumer loop) needs only `httpx` + `septum-queue`. The FastAPI `/health` endpoint lives behind the `[server]` extra so a bare worker process stays lean.

## Install

```bash
pip install -e packages/gateway                    # Consumer + forwarders only
pip install -e "packages/gateway[server]"          # Adds FastAPI + uvicorn for /health
pip install -e "packages/gateway[test]"            # pytest + respx for HTTP mocking
```

## Providers

| Provider | Forwarder | Default URL |
|---|---|---|
| Anthropic Messages API | `AnthropicForwarder` | `https://api.anthropic.com/v1/messages` |
| OpenAI Chat Completions | `OpenAIForwarder` | `https://api.openai.com/v1/chat/completions` |
| OpenRouter | `OpenRouterForwarder` | `https://openrouter.ai/api/v1/chat/completions` |

Envelope-carried `base_url` wins over the default, so an operator can point any forwarder at a proxy (enterprise-side egress control) without shipping a new gateway build.

## Usage

```python
import asyncio
from septum_gateway import GatewayConfig, GatewayConsumer, ForwarderRegistry
from septum_queue import FileQueueBackend

config = GatewayConfig.from_env()
registry = ForwarderRegistry.from_config(config)

request_queue = FileQueueBackend("/var/septum/queue", topic=config.request_topic)
response_queue = FileQueueBackend("/var/septum/queue", topic=config.response_topic)

consumer = GatewayConsumer(
    request_queue=request_queue,
    response_queue=response_queue,
    registry=registry,
)

asyncio.run(consumer.run_forever())
```

## Environment

| Variable | Default | Purpose |
|---|---|---|
| `SEPTUM_GATEWAY_REQUEST_TOPIC` | `septum.llm.requests` | Queue topic the consumer listens on |
| `SEPTUM_GATEWAY_RESPONSE_TOPIC` | `septum.llm.responses` | Queue topic replies are published to |
| `SEPTUM_GATEWAY_ANTHROPIC_API_KEY` / `ANTHROPIC_API_KEY` | — | Default Anthropic key if the envelope carries none |
| `SEPTUM_GATEWAY_OPENAI_API_KEY` / `OPENAI_API_KEY` | — | Default OpenAI key |
| `SEPTUM_GATEWAY_OPENROUTER_API_KEY` / `OPENROUTER_API_KEY` | — | Default OpenRouter key |
| `SEPTUM_GATEWAY_TIMEOUT_SECONDS` | `30` | Total deadline for one upstream attempt, covering the whole request |
| `SEPTUM_GATEWAY_MAX_ATTEMPTS` | `3` | Retry ceiling on transient errors |

Worst case for one request with the defaults: 3 attempts × 30 s, plus two `Retry-After` waits capped at 10 s, about 110 s. The api's `SEPTUM_GATEWAY_CLIENT_TIMEOUT_SECONDS` (default 120) must stay above that bound; raise it when you raise either value here.

## Queue

Queue entries are acknowledged and deleted once processed. The queue Redis in the compose files runs with persistence disabled (`--save ""`, `--appendonly no`), so it writes no snapshot or append-only file, and it has no data volume. A queue Redis restart fails in-flight requests, and their callers get an error. The queue Redis also caps memory with `SEPTUM_QUEUE_REDIS_MAXMEMORY` (default `256mb`) and refuses new writes when full, so queued messages are never evicted.

The queue link is plaintext unless the URL is `rediss://` and the Redis listener terminates TLS. The client then verifies the certificate and the host name.

## License

MIT
