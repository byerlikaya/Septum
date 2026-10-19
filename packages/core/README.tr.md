# septum-core

> 🇬🇧 [English version](README.md)

Gizlilik öncelikli PII tespit, maskeleme ve geri yazma motoru. [Septum](https://github.com/byerlikaya/Septum) projesinin atan kalbidir.

## Ne yapar?

- **Tespit eder** — metindeki kişisel verileri Presidio, spaCy/Transformers NER ve regülasyona özgü tanıyıcı paketleri üzerinden bulur.
- **Maskeler** — tespit edilen her varlığı deterministik placeholder'lara (`[PERSON_NAME_1]`, `[EMAIL_ADDRESS_2]` vb.) çevirir, eşlemeyi bellekte taşır.
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

## Model sabitleme

Varsayılan NER, embedding ve OCR modelleri Hugging Face commit'lerine sabitlenmiştir; yüklenmeden önce her dosya SHA256 ile denetlenir. Çevrimdışı çalışmada `HF_HUB_OFFLINE=1` kullanılır; bu, önbellekte olmayan ya da doğrulanmamış hiçbir şeyi kabul etmez. Fetch CLI'si (`python -m septum_api.cli.models fetch`) `septum-api` içindedir; bkz. [mimari](../../docs/tr/architecture.md#model-bütünlüğü).

## Kullanım

```python
from septum_core import SeptumEngine

engine = SeptumEngine(regulations=["gdpr", "kvkk"])

result = engine.mask("Elif Demir'in TC kimlik numarası 10000000146.")
# result.masked_text        → "[PERSON_NAME_1]'in TC kimlik numarası [NATIONAL_ID_1]."
# result.session_id         → "sess_abc123"
# result.entity_type_counts → {"PERSON_NAME": 1, "NATIONAL_ID": 1}

restored = engine.unmask(
    "[PERSON_NAME_1] kontrol randevusunu onayladı.",
    session_id=result.session_id,
)
# restored → "Elif Demir kontrol randevusunu onayladı."
```

## Lisans

MIT
