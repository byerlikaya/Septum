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

## Saklama

İki sınır yalnızca canlı JSONL defterini budar: `SEPTUM_AUDIT_RETENTION_DAYS` (yaş) ve `SEPTUM_AUDIT_RETENTION_MAX_RECORDS` (kayıt sayısı). Ayarlanmayan bir sınır her şeyi tutar. Budama başlangıçta, sonra her etkin dönemde bir kez yapılır; etkin dönem, yapılandırılmış `SEPTUM_AUDIT_RETENTION_INTERVAL_SECONDS` değeridir (varsayılan `3600`) ya da bir tur yavaşsa daha uzundur. `/health`, `ok`, `disabled` ve `not_reported` için 200; `halted`, `failed`, `blocked`, `stale`, `missing` ve `unreadable` için 503 döner. Tüketici `consumer` alanını ekler: `ok` ya da `not_reported` için 200, `consumer_failing` ya da `consumer_unreadable` için 503 döner. `halted` defterin ve anchor'ın doğrulanmış bir kopyadan geri yüklenmesi gerektiği anlamına gelir. `/health` artık `sink_path` döndürmez. Ayrıntılı başvuru: kurulum rehberindeki "Denetim olayı saklama" bölümü.

- Hiçbir şey içeriği nedeniyle silinmez. `attributes` hash'lenmeden önce normalleştirilir: anahtarlar metne, iki öğeli çiftlerden oluşan liste sözlüğe dönüşür, diğer sözlük olmayan değerler `{"value": ...}` içine alınır, üst düzeydeki çakışan anahtarlar `"value"` altında liste olarak tutulur ve seri hale getirilemeyen değer `"<unserialisable>"` olur. Sonlu olmayan sayılar metne dönüşür. Sıfır, boolean, metin, eksik, aralık dışı ya da sonlu olmayan bir `timestamp` alım zamanıyla değiştirilir ve özgün değer tutulmaz. Bunların her biri kayıt düzeyindeki `ingest_replaced` işaretini `true` yapar (bir değerin değiştirildiğini söyler, hangi alanın olduğunu söylemez). Sink'in saklayamadığı bir kayıt, id (64 karaktere kesilmiş), source, olay türü ve correlation id korunarak bir yer tutucu olarak tutulur; içerik tutulmaz. Consumer, nesne olmayan bir payload'ı malformed olarak onaylar ve loglar; hiçbir şey yazmaz. Diğer her hata mesajı yeniden kuyruğa alınır; yeniden denemeler 2 saniyeden başlayıp her seferinde iki katına çıkar ve 60 saniyede sınırlanır; hata ilk denemede, sonra her 10. denemede loglanır. Sink'in reddettiği bir kayıt yer tutucu olarak tutulur ve yalnızca yazıldıktan sonra onaylanır; bu yazma başarısız olursa mesaj yeniden kuyruğa alınır. 32 seviyeden daha derin iç içe nesneler `"<depth limit>"` ile değiştirilir. Sink, `ingest_replaced` ve `ingest_unstorable` işaretlerinin üretici kopyasını atar; böylece üretici bu işaretlerden birini sahteleyemez. Dışa aktarımlar bunları metin olarak yazar; böylece çıktı katı JSON kalır. Sonlu olmayan değerlerin metin olarak yazıldığı dışa aktarım satırları `verify_chain` ile yeniden doğrulanmaz.

## Lisans

MIT — [LICENSE](../../LICENSE) dosyasına bakın.
