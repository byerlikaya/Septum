# Septum — Özel Tanıyıcı Kuralları

<p align="center">
  <a href="../../readme.tr.md"><strong>🏠 Anasayfa</strong></a>
  &nbsp;·&nbsp;
  <a href="installation.md"><strong>🚀 Kurulum</strong></a>
  &nbsp;·&nbsp;
  <a href="benchmark.md"><strong>📈 Benchmark</strong></a>
  &nbsp;·&nbsp;
  <a href="features.md"><strong>✨ Özellikler</strong></a>
  &nbsp;·&nbsp;
  <a href="architecture.md"><strong>🏗️ Mimari</strong></a>
  &nbsp;·&nbsp;
  <a href="document-ingestion.md"><strong>📊 Doküman İngest</strong></a>
  &nbsp;·&nbsp;
  <a href="screenshots.md"><strong>📸 Ekran Görüntüleri</strong></a>
</p>

---

Septum kutudan 17 regülasyon paketi ile gelir. **Özel kurallar** kod tabanını
forklamadan tespiti genişletmenizi sağlar: regex, bağlam anahtarlı kelime
listesi veya LLM prompt'lu bir kural ekleyin; aktif sanitization pipeline'ına
anında katılır. Kurallar **Ayarlar → Regülasyonlar → Özel Kurallar** altında
yaşar ve yerel veritabanında saklanır.

Bu sayfa çalışan bir referans. Her tespit yöntemi için işlenmiş örnek + test
adımları var.

## Tespit yöntemleri

| Yöntem        | Ne zaman kullanılır                                                                              | Maliyet      |
|:--------------|:-------------------------------------------------------------------------------------------------|:-------------|
| `regex`       | Kararlı şekli olan yapısal tanımlayıcılar (kodlar, ön ekli numaralar, formatlı ID'ler).         | Mikrosaniye  |
| `keyword_list` | Her zaman maskelenmesi gereken kısa, sınırlı kelime listesi (proje kod adları, takım adları).   | Mikrosaniye  |
| `llm_prompt`  | Regex'le yakalanamayan serbest metin kategorileri (tıbbi durumlar, hassas görüşler, lakaplar).  | ~Saniye (yerel Ollama) |

Her kuralda ortak alanlar:

- **İsim** — denetim kaydında ve tespit listesinde görünen kısa etiket.
- **Varlık tipi** — örn. `INTERNAL_PROJECT_CODE`, `EMPLOYEE_BADGE`. Serbest;
  placeholder formatı `[<VARLIK_TIPI>_N]` olur.
- **Placeholder etiketi** *(opsiyonel)* — maskelenmiş metinde görünecek
  placeholder'ı override eder (varsayılan: varlık tipi).
- **Aktif** — silmeden devre dışı bırakır.
- **Örnek metin** — panel içindeki **Test** düğmesinin kullandığı alan.

## Yöntem 1 — Regex

Regex kuralı chunk metnini tarar ve her eşleşme için bir span üretir. En hızlı
tespit yolu; ilk başvurmanız gereken yöntem.

### Örnek: dahili proje kodu

Şirket her dahili projeyi `PRJ-<yıl>-<5 hane>` ile etiketliyor (örn.
`PRJ-2024-04829`). HR mektupları, faturalar, Slack export'larında geçer.

| Alan              | Değer                              |
|:------------------|:-----------------------------------|
| İsim              | `Dahili proje kodu`                |
| Varlık tipi       | `INTERNAL_PROJECT_CODE`            |
| Tespit yöntemi    | `regex`                            |
| Desen             | `\bPRJ-\d{4}-\d{5}\b`              |
| Placeholder etiketi | `INTERNAL_PROJECT_CODE`          |
| Örnek metin       | `Bütçe PRJ-2024-04829 onaylandı.` |

Kaydet sonrası **Test** tuşu
`POST /api/regulations/recognizers/{id}/test` çağırır; başarılı bir test
`1 eşleşme — PRJ-2024-04829 (skor 0.85)` raporlar.

> **İpucu — bağlam çapası**
> Deseni `\b...\b` (kelime sınırları) içine sarın ve gerçek ID'nin önüne
> bir anahtar kelime koyun (`(?:Proje|PRJ)[-\s:]*(\d{4}-\d{5})`). Sınır
> çapaları regex'in daha büyük string'ler içinde patlamasını engeller.

### Örnek: checksum'lı HR rozeti

ID'leriniz checksum içeriyorsa regex eşleştirmeyi yapar fakat checksum
doğrulamasını yapamaz. Saf regex kuralları checksum kontrolü yapmaz; bunun
için kodun içinde recognizer yazmanız gerekir.

## Yöntem 2 — Kelime listesi

Kelime kuralı her literal eşleşme için bir span üretir. Her kelime kelime-sınır
substring olarak eşleştirilir; varsayılan büyük/küçük harf duyarsızdır.

### Örnek: kod adı maskeleme

Bir takım dahili kod adları kullanıyor (`Project Bluebird`, `Operation Halcyon`,
`Goldfish Initiative`). Hiçbir regülasyona göre PII değil ama şirket bulut
LLM çağrısı öncesi maskelemek istiyor.

| Alan              | Değer                                                          |
|:------------------|:---------------------------------------------------------------|
| İsim              | `Dahili kod adları`                                            |
| Varlık tipi       | `INTERNAL_CODENAME`                                            |
| Tespit yöntemi    | `keyword_list`                                                 |
| Anahtar kelimeler | `Project Bluebird, Operation Halcyon, Goldfish Initiative`     |
| Placeholder etiketi | `INTERNAL_CODENAME`                                          |
| Örnek metin       | `Project Bluebird lansmanı ertelendi.`                         |

Kaydet sonrası **Test** `1 eşleşme — Project Bluebird` rapor eder.

> **İpucu — kelime yoğunluğu**
> Listeyi ~50 girişin altında tutun. Uzun listeler ingestion'ı doğrusal
> yavaşlatır ve recognizer başına tüm liste belleğe yüklenir. Yüzlerce
> terim için alternation'lı regex daha iyi seçim.

## Yöntem 3 — LLM prompt

LLM-prompt kuralları chunk metni + bir talimatı yerel Ollama modeline yollar
ve JSON cevabını span'lere parse eder. Regex ile yakalanamayan kategoriler
için doğru araç — tıbbi durumlar, görüşler, hassas kültürel işaretler,
lakaplar — ama `llama3.2:3b` ile chunk başına `~1–5s`, daha büyük
modellerde orantılı olarak daha fazla maliyet getirir.

### Örnek: ilaç adı tespiti

Klinik bir doküman setinde Presidio'nun yakalamadığı serbest metin ilaç
referansları geçiyor (marka, jenerik, doz formları).

| Alan              | Değer                                                                                            |
|:------------------|:-------------------------------------------------------------------------------------------------|
| İsim              | `İlaç referansları`                                                                              |
| Varlık tipi       | `MEDICATION`                                                                                     |
| Tespit yöntemi    | `llm_prompt`                                                                                     |
| LLM prompt'u      | `Find every medication mention (brand or generic name, with or without dose). Return JSON: [{"text": "<mention>"}].` |
| Placeholder etiketi | `MEDICATION`                                                                                   |
| Örnek metin       | `Hasta günlük 5 mg Coumadin ve ihtiyaç halinde ibuprofen alıyor.`                              |

**Test** prompt'u örnek üzerinde çalıştırır; yerel model sağlıklıysa
`2 eşleşme — Coumadin 5 mg, ibuprofen` rapor eder.

> **İpucu — JSON sözleşmesini minimum tutun**
> Çok alanlı (severity, doz, vb.) sorulan prompt'lar daha az güvenilir JSON
> döndürür. Recognizer sadece eşleşen metni ister; geri kalan parse hatalarını
> artıran gürültüdür.

> **İpucu — bağlam penceresi**
> Septum tek seferde bir chunk gönderir (varsayılan `chunk_size = 800` karakter).
> Prompt'u "tüm doküman" yerine "bir metin bloğu" üzerinde çalışacak şekilde
> ifade edin ki chunk'lar arası tutarlı davransın.

## Ortak alanlar & test döngüsü

Her özel kural şu ek kontrolleri paylaşır:

- **Bağlam kelimeleri** *(opsiyonel)* — kuralın tetiklenmesi için aynı chunk
  içinde geçmesi gereken ek kelimeler. Aşırı false-positive üreten regex'leri
  bastırmak için kullanın. Örnek: 11 haneli ID'lere ait bir kural `Müşteri`
  veya `Customer` bağlam kelimesi gerektirebilir.
- **Aktif** — silmeden devre dışı.
- **Örnek metin** + **Test** — inline regresyon kontrolü. Her düzenlemeden
  sonra kullanın; veri kalıcılaştırmaz, hem pozitif hem negatif vakaları
  hızlıca doğrulamanın yolu.

Test endpoint'i ingestion'ın kullandığı kod yolunu çağırır; "test'te çalışıyor"
demek "gerçek dokümanlarda çalışıyor" demektir — chunk metni örneğe benziyorsa.

## Denetim & hata ayıklama

Özel recognizer'dan gelen her tespit:

1. Dokümanın `entity_detections` tablosuna kuralın `entity_type` + `placeholder`
   değeriyle düşer — **Doküman önizleme → Tespit edilen varlıklar** altında
   görünür.
2. İngest tamamlandığında **Ayarlar → Denetim Kaydı** sayfasında bir satır
   olur; `extra` alanında kural adıyla etiketlenmiş.
3. Standart absorpsiyon + dedup süreçlerinden geçer — özel kural built-in
   regülasyon tespitlerini override **etmez**. Span önceliği Septum'un
   geri kalanı için kullanılan aynı `_HIGH_PRIORITY_ENTITY_TYPES` tablosu
   tarafından belirlenir.

Bir kural test'te ateşliyor ama gerçek dokümanda ateşlemiyorsa, doküman
önizlemeyi açıp chunk metnini kontrol edin — PDF extraction bazen hedefi
chunk'lar arasına böler; bu durumda `chunk_size`'ı yükseltin veya
`chunk_overlap`'i küçültün.
