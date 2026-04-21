# septum-queue

Cross-zone message queue for [Septum](https://github.com/byerlikaya/Septum). Transports already-masked LLM requests and responses between the air-gapped `septum-api` and the internet-facing `septum-gateway`. Raw PII never enters a queue envelope — the producer has already run every payload through `septum-core` before it reaches this layer.

## Why it exists

Septum splits into two trust zones:

- **Air-gapped** (`septum-core` + `septum-api` + `septum-web` + `septum-mcp`) — has access to raw documents, performs PII masking.
- **Internet-facing** (`septum-gateway` + `septum-audit`) — forwards masked requests to cloud LLMs, never sees raw PII.

`septum-queue` is the bridge. It defines the abstract transport interface both sides implement against, plus the envelope dataclasses that shape every payload crossing the boundary.

## Design guarantees

- **Zero internal dependencies.** The core package has no runtime deps outside the Python stdlib. Backend-specific clients (Redis, …) are gated behind optional extras.
- **Never imports `septum-core`.** Same air-gap reasoning as the gateway — the queue layer is a dumb transport, it must not acquire PII-aware semantics.
- **JSON-only payloads.** Every envelope serializes with `json.dumps`, so any backend (file, Redis streams, HTTP) can store it without a custom codec.
- **At-least-once delivery.** Concrete backends ack / nack explicitly; no backend offers exactly-once.

## Install

```bash
pip install -e packages/queue                # File backend (stdlib only)
pip install -e "packages/queue[redis]"       # Redis streams backend
pip install -e "packages/queue[test]"        # pytest + pytest-asyncio
```

## Usage

```python
from septum_queue import (
    FileQueueBackend,
    QueueSession,
    RequestEnvelope,
    ResponseEnvelope,
)

# Producer (runs inside septum-api)
async with QueueSession(FileQueueBackend("/var/septum/queue", topic="llm-requests")) as q:
    envelope = RequestEnvelope.new(
        provider="anthropic",
        model="claude-sonnet-4",
        messages=[{"role": "user", "content": "[PERSON_1]'s mailing address?"}],
    )
    await q.backend.publish(envelope.__dict__)

# Consumer (runs inside septum-gateway)
async with QueueSession(FileQueueBackend("/var/septum/queue", topic="llm-requests")) as q:
    async for message in q.backend.consume(block_ms=5000):
        request = RequestEnvelope.from_dict(message.payload)
        # ... forward to cloud LLM, get masked reply ...
        await q.backend.ack(message.id)
```

## Backends

| Backend | Module | When to use | Extra |
|---|---|---|---|
| `FileQueueBackend` | `septum_queue.file_backend` | Air-gapped deployments, local dev, tests — no infrastructure dependency | — |
| `RedisStreamsQueueBackend` | `septum_queue.redis_backend` | Shared-infrastructure deployments with at-least-once consumer groups | `[redis]` |

## License

MIT
