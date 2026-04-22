# septum-gateway

> 🇬🇧 [English version](README.md)

[Septum](https://github.com/byerlikaya/Septum) için internet-facing LLM yönlendiricisi. `septum-queue` üzerinden gelen maskelenmiş sohbet isteklerini tüketir ve Anthropic, OpenAI, OpenRouter gibi bulut LLM sağlayıcılarına iletir. Gateway sürecine ham PII asla sızmaz — air-gapped `septum-api`, her payload'ı kuyruğa girmeden önce `septum-core` üzerinden maskeler.

## Tasarım garantileri

- **`septum-core`'u asla import etmez.** Gateway internet-facing bölgede yaşar ve ham PII'ye bakmak için hiçbir gerekçesi yoktur. Bu duvar yalnızca dağıtımla değil, kod gözden geçirmesiyle de korunur — ileride biri `from septum_core import ...` eklemeye kalkarsa refactor lint kuralı bu değişikliği engeller.
- **Stateless.** Veritabanı, session deposu ya da cache yoktur. Her istek saf bir fonksiyondur: envelope girer, envelope çıkar.
- **Sağlayıcıdan bağımsız.** Yeni bir bulut sağlayıcı eklemek, `_OpenAICompatibleForwarder`'dan tek bir subclass türetmek (API OpenAI şekilli değilse sıfırdan bir `BaseForwarder`) kadar kolaydır.
- **Web sunucusu opsiyoneldir.** Temel kullanım (tüketici döngüsü) yalnızca `httpx` + `septum-queue` ister. `/health` uç noktasını sunan FastAPI `[server]` extra'sıyla gelir; böylece yalın bir worker süreci hafif kalır.

## Kurulum

```bash
pip install -e packages/gateway                    # Yalnız tüketici + forwarder'lar
pip install -e "packages/gateway[server]"          # /health için FastAPI + uvicorn ekler
pip install -e "packages/gateway[test]"            # HTTP mock için pytest + respx
```

## Sağlayıcılar

| Sağlayıcı | Forwarder | Varsayılan URL |
|---|---|---|
| Anthropic Messages API | `AnthropicForwarder` | `https://api.anthropic.com/v1/messages` |
| OpenAI Chat Completions | `OpenAIForwarder` | `https://api.openai.com/v1/chat/completions` |
| OpenRouter | `OpenRouterForwarder` | `https://openrouter.ai/api/v1/chat/completions` |

Envelope'un taşıdığı `base_url`, varsayılanı geçersiz kılar; bu sayede bir operator yeni bir gateway build'i çıkarmadan herhangi bir forwarder'ı bir proxy'ye yönlendirebilir (kurumsal çıkış kontrolü gibi).

## Kullanım

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

## Ortam değişkenleri

| Değişken | Varsayılan | Amaç |
|---|---|---|
| `SEPTUM_GATEWAY_REQUEST_TOPIC` | `septum.llm.requests` | Tüketicinin dinlediği kuyruk topic'i |
| `SEPTUM_GATEWAY_RESPONSE_TOPIC` | `septum.llm.responses` | Cevapların yayınlandığı kuyruk topic'i |
| `SEPTUM_GATEWAY_ANTHROPIC_API_KEY` / `ANTHROPIC_API_KEY` | — | Envelope içinde anahtar yoksa kullanılan varsayılan Anthropic anahtarı |
| `SEPTUM_GATEWAY_OPENAI_API_KEY` / `OPENAI_API_KEY` | — | Varsayılan OpenAI anahtarı |
| `SEPTUM_GATEWAY_OPENROUTER_API_KEY` / `OPENROUTER_API_KEY` | — | Varsayılan OpenRouter anahtarı |
| `SEPTUM_GATEWAY_TIMEOUT_SECONDS` | `30` | İstek başına HTTP zaman aşımı |
| `SEPTUM_GATEWAY_MAX_ATTEMPTS` | `3` | Geçici hatalarda yeniden deneme tavanı |

## Lisans

MIT
