# septum-queue

> 🇬🇧 [English version](README.md)

[Septum](https://github.com/byerlikaya/Septum) için bölgeler arası mesaj kuyruğu. Air-gapped `septum-api` ile internet-facing `septum-gateway` arasında, önceden maskelenmiş LLM istek ve cevaplarını taşır. Kuyruk zarfına ham PII asla girmez — üretici, bu katmana ulaşmadan önce her payload'ı `septum-core`'dan geçirir.

## Neden var?

Septum iki güven bölgesine ayrılır:

- **Air-gapped** (`septum-core` + `septum-api` + `septum-web` + `septum-mcp`) — ham dokümanlara erişir ve PII maskelemesini yapar.
- **Internet-facing** (`septum-gateway` + `septum-audit`) — maskelenmiş istekleri bulut LLM'lere iletir; ham PII'yi asla görmez.

`septum-queue` bu iki bölgeyi birbirine bağlayan köprüdür. Her iki tarafın karşı koyacağı soyut taşıma arayüzünü ve sınırı geçen her payload'ın biçimini belirleyen envelope dataclass'larını tanımlar.

## Tasarım garantileri

- **İç bağımlılık yoktur.** Çekirdek paketin Python standart kütüphanesi dışında çalışma zamanı bağımlılığı yoktur. Backend'e özgü istemciler (Redis vb.) opsiyonel extra'ların arkasında kalır.
- **`septum-core`'u asla import etmez.** Gateway ile aynı air-gap gerekçesi — kuyruk katmanı kasıtlı olarak "aptal" bir taşımadır; PII'ye duyarlı anlam taşımamalıdır.
- **Yalnız JSON payload.** Her envelope `json.dumps` ile serileştirilir; bu sayede her backend (dosya, Redis streams, HTTP) herhangi bir özel codec'e ihtiyaç duymadan saklayabilir.
- **En az bir kez teslim.** Somut backend'ler ack / nack işlemlerini açıkça yapar; tam-olarak-bir kez garantisi sunan backend yoktur.

## Kurulum

```bash
pip install -e packages/queue                # Dosya backend'i (yalnız stdlib)
pip install -e "packages/queue[redis]"       # Redis streams backend'i
pip install -e "packages/queue[test]"        # pytest + pytest-asyncio
```

## Kullanım

```python
from septum_queue import (
    FileQueueBackend,
    QueueSession,
    RequestEnvelope,
    ResponseEnvelope,
)

# Üretici (septum-api içinde çalışır)
async with QueueSession(FileQueueBackend("/var/septum/queue", topic="llm-requests")) as q:
    envelope = RequestEnvelope.new(
        provider="anthropic",
        model="claude-sonnet-4",
        messages=[{"role": "user", "content": "[PERSON_1]'ın posta adresi?"}],
    )
    await q.backend.publish(envelope.__dict__)

# Tüketici (septum-gateway içinde çalışır)
async with QueueSession(FileQueueBackend("/var/septum/queue", topic="llm-requests")) as q:
    async for message in q.backend.consume(block_ms=5000):
        request = RequestEnvelope.from_dict(message.payload)
        # ... bulut LLM'e iletir, maskelenmiş cevabı alır ...
        await q.backend.ack(message.id)
```

## Backend'ler

| Backend | Modül | Ne zaman kullanılır | Extra |
|---|---|---|---|
| `FileQueueBackend` | `septum_queue.file_backend` | Air-gapped dağıtım, yerel geliştirme, testler — hiçbir altyapı bağımlılığı yok | — |
| `RedisStreamsQueueBackend` | `septum_queue.redis_backend` | Paylaşılan altyapıda, en-az-bir-kez tüketici grupları ile | `[redis]` |

## Lisans

MIT
