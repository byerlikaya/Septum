# septum-core

> 🇬🇧 [English version](README.md)

Gizlilik öncelikli PII tespit, maskeleme ve geri yazma motoru. [Septum](https://github.com/byerlikaya/Septum) projesinin atan kalbidir.

## Ne yapar?

- **Tespit eder** — metindeki kişisel verileri Presidio, spaCy/Transformers NER ve regülasyona özgü tanıyıcı paketleri üzerinden bulur.
- **Maskeler** — tespit edilen her varlığı deterministik placeholder'lara (`[PERSON_1]`, `[EMAIL_2]` vb.) çevirir, eşlemeyi bellekte taşır.
- **Geri yazar** — LLM cevabı geldiğinde placeholder'ları eşleme üzerinden gerçek değerlere yeniden döner.
- **Birleştirir** — birden fazla gizlilik regülasyonunu (GDPR, KVKK, HIPAA, CCPA, LGPD, …) tek bir tespit hattı altında toplar; çakışmada en kısıtlayıcı kural geçerli olur.

## Tasarım garantileri

- **Ağ bağımlılığı yoktur.** `septum-core`, `httpx`, `requests`, `urllib`, `aiohttp` ya da başka hiçbir HTTP istemcisini import etmez. Air-gapped donanım üzerinde güvenle çalışır.
- **Veritabanına bağlı değildir.** Motor, regülasyon verilerini düz dataclass'lar üzerinden önceden yüklenmiş olarak alır. Veritabanı erişimi buraya değil, `septum-api`'ye aittir.
- **Semantik katman takılıp çıkarılabilir.** Opsiyonel LLM destekli PII tespiti (örneğin Ollama üzerinden) `SemanticDetectionPort` protokolü aracılığıyla enjekte edilir; doğrudan import edilmez.

## Kurulum

```bash
pip install -e packages/core
```

HuggingFace NER katmanı için:

```bash
pip install -e "packages/core[transformers]"
```

## Kullanım

```python
from septum_core import SeptumEngine

engine = SeptumEngine(regulations=["gdpr", "kvkk"])

result = engine.mask("Ahmet Yılmaz'ın TC numarası 12345678901.")
# result.masked_text → "[PERSON_1]'ın TC numarası [TCKN_1]."
# result.session_id  → "sess_abc123"
# result.entities    → [{"type": "PERSON", "original": "Ahmet Yılmaz", ...}, ...]

restored = engine.unmask(
    "[PERSON_1] paketin tesliminin tamamlandığını onayladı.",
    session_id=result.session_id,
)
# restored → "Ahmet Yılmaz paketin tesliminin tamamlandığını onayladı."
```

## Lisans

MIT
