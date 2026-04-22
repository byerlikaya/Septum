# septum-audit

> 🇬🇧 [English version](README.md)

Septum için internet-facing uyumluluk denetim kaydı. Zaten maskelenmiş olay kayıtlarını saklar; aşağı akışta SIEM hatlarına iletmek için JSON, CSV ya da Splunk HEC formatında dışa aktarır.

`septum-audit`, `septum-gateway` ile yan yana **internet-facing bölgede** yaşar. Tasarımı gereği `septum-core`'u asla import etmez: gördüğü her kayıt, air-gapped üreticisi tarafından ham PII'den çoktan arındırılmıştır. Bu sözleşmeyi kod gözden geçirmesinde uygulanabilir kılan şey tam olarak paketler arasına çekilen bu bağımlılık duvarıdır.

## Kurulum

```bash
pip install septum-audit                  # taban: yalnız stdlib sink'ler + exporter'lar
pip install "septum-audit[queue]"         # septum-queue tüketicisini ekler
pip install "septum-audit[server]"        # FastAPI export uç noktasını ekler
```

## Hızlı başlangıç

```python
from septum_audit import (
    AuditRecord,
    JsonlFileSink,
    JsonExporter,
    RetentionPolicy,
    apply_retention_to_jsonl,
)

sink = JsonlFileSink("/var/log/septum/audit.jsonl")
await sink.write(AuditRecord(
    source="septum-api",
    event_type="pii.detected",
    correlation_id="req-123",
    attributes={"entity_count": 4, "regulation_ids": ["gdpr", "kvkk"]},
))

# Splunk HEC'e akıt
import sys
JsonExporter().write(sink.read_all(), sys.stdout)

# 90 günden eski kayıtları at, üst sınırı 1M satıra koy
apply_retention_to_jsonl(
    "/var/log/septum/audit.jsonl",
    RetentionPolicy(max_age_days=90, max_records=1_000_000),
)
```

## Mimari

| Bileşen | Dosya | Açıklama |
|---|---|---|
| `AuditRecord` | `events.py` | Değişmez zarf; `source`, `event_type`, `correlation_id`, `attributes` |
| `AuditSink` (Protocol) | `sink.py` | Her backend'in uyması gereken yazma sözleşmesi |
| `JsonlFileSink` | `sink.py` | Append-only newline-delimited JSON; logrotate dostu |
| `MemorySink` | `sink.py` | Süreç içi liste; testler ve geçici sayımlar için |
| `JsonExporter` / `CsvExporter` / `SplunkHecExporter` | `exporters/` | Formata özgü byte yazıcıları |
| `RetentionPolicy` | `retention.py` | Yaş + satır üst sınırı; atomik yerinde yeniden yazım |
| `AuditConsumer` | `consumer.py` *(queue extra'sı)* | `septum-queue` topic'ini bir sink'e akıtır |
| `create_app()` | `main.py` *(server extra'sı)* | `/health` ve `/api/audit/export` ile FastAPI |

## Bölge disiplini

```
┌─────────────────────────┐    kuyruk    ┌──────────────────────────┐
│  Air-gapped bölge       │    topic     │  Internet-facing bölge   │
│  septum-api / -core     ├────────────►│  septum-gateway           │
│                         │ (maskeli)    │  septum-audit             │
└─────────────────────────┘              └──────────────────────────┘
```

`septum-audit`, yalnızca `septum-gateway`'in kabul ettiği kayıtları görür: istek başına küçük bir zarf — sağlayıcı / model / correlation id / durum / gecikme. Prompt yok, cevap yok, doküman içeriği yok.

## Lisans

MIT — [LICENSE](../../LICENSE) dosyasına bakın.
