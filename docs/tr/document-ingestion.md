# Doküman İşleme Akışı

<p align="center">
  <a href="./index.md"><strong>🏠 Ana Sayfa</strong></a>
  &nbsp;·&nbsp;
  <a href="installation.md"><strong>🚀 Kurulum</strong></a>
  &nbsp;·&nbsp;
  <a href="benchmark.md"><strong>📈 Benchmark</strong></a>
  &nbsp;·&nbsp;
  <a href="features.md"><strong>✨ Özellikler</strong></a>
  &nbsp;·&nbsp;
  <a href="architecture.md"><strong>🏗️ Mimari</strong></a>
  &nbsp;·&nbsp;
  <strong>📊 Doküman İşleme</strong>
  &nbsp;·&nbsp;
  <a href="screenshots.md"><strong>📸 Ekran Görüntüleri</strong></a>
</p>

---

Septum, yüklenen bir dosyayı aranabilir ve anonimleştirilmiş içeriğe dönüştürür. Tüm adımlar **yerelde** çalışır — ham PII makineden çıkmaz.

<p align="center">
  <a href="#doküman-i̇şleme-akışı"><img src="../assets/document-ingestion.tr.svg" alt="Septum doküman işleme akışı — yükleme, içerik-bazlı tip tespiti, formata özgü ingester (PDF / DOCX / OCR / Whisper), dil tespiti, üç katmanlı PII tespiti (Presidio + NER + opsiyonel Ollama), anonimleştirme haritası ile maskeleme, paralel chunking ve şifreli saklama, FAISS ve BM25 indekslerine embedding, arama hazır" width="820" /></a>
</p>

## Adımlar

1. **Yükleme** — Dosya API'ye ulaştığında tipi uzantıdan değil, içerik baytlarından okunur (`python-magic`). Uzantısı `.pdf` olan ama aslında PNG olan bir dosya doğrudan OCR hattına düşer.

2. **Formata göre ingester** — PDF'ler `pypdf` ile sayfa sayfa okunur; DOCX ve XLSX için `python-docx` ve `openpyxl` devreye girer; görseller ve taranmış PDF'ler PaddleOCR'dan, ses dosyaları OpenAI Whisper'dan geçer.

3. **Dil tespiti** — Çıkarılan metin 20+ dil arasında sınıflandırılır. Sonraki adımda kullanılacak NER modeli ve validator kuralları bu dile göre seçilir.

4. **Üç katmanlı PII tespiti** — Üç detektör sırayla çalışır ve çıktıları birleştirilir:
   - **Presidio** — regex desenleri ve algoritmik validator'lar (TCKN checksum, Aadhaar Verhoeff, NRIC/FIN, CPF, NINO, CNPJ, My Number ve diğerleri).
   - **NER** — `PERSON_NAME`, `LOCATION`, `ORGANIZATION_NAME` için dil grubuna göre seçilen transformer modeli. Kapsam dillere göre değişir; bkz. [NER model kapsamı](#ner-model-kapsamı).
   - **Ollama** — desenlerin yakalayamadığı, bağlama bağlı PII için opsiyonel semantik katman. Varsayılan olarak kapalıdır; Ayarlar → Gizlilik ve Anonimleştirme → *Ollama semantik tespit* seçeneğinden açılır. Yeni kurulumdaki varsayılanı `USE_OLLAMA_SEMANTIC_LAYER_DEFAULT` belirler; bu değer ayar kaydı oluşturulurken bir kez okunur.

   Üç detektörün bulduğu çakışan span'ler birleştirilir; coreference çözümlenerek "Elif" ile "Hanım Demir" aynı placeholder numarasına bağlanır.

5. **Maskeleme ve anonimleştirme haritası** — Tespit edilen her varlık, tipe göre numaralanmış tutarlı bir placeholder ile değiştirilir (`[PERSON_NAME_1]`, `[EMAIL_ADDRESS_3]`). `original → placeholder` haritası her doküman için ayrı tutulur, diskte şifreli saklanır ve air-gapped bölgeden asla dışarı çıkmaz.

6. **Paralel işleme** — Maskeli metin üretildikten sonra iki akış eşzamanlı koşar:
   - **Chunking → Embedding → FAISS + BM25** — Maskeli metin, paragraf sınırlarına sadık kalarak ve aralarında örtüşme bırakılarak chunk'lara bölünür. Her chunk sentence-transformers ile embed edilir; sonuçlar hem FAISS vektör indeksine hem BM25 keyword indeksine yazılır.
   - **Şifreli saklama** — Orijinal dosya AES-256-GCM ile mühürlenerek diske yazılır; air-gapped bölge dışında asla deşifre edilmez.

7. **Arama hazır** — FAISS, BM25 ve şifreli dosya yazımı tamamlandığında doküman `ingestion_status="completed"` olarak işaretlenir ve sohbet üzerinden sorgulanabilir hale gelir.

## NER model kapsamı

NER katmanı, her dil için bir model çalıştırır (Türkçe için iki model). Model, 3. adımda tespit edilen dile göre seçilir. Kapsam diller arasında eşit değildir:

| Dil | Model | Not |
|:---|:---|:---|
| `tr` | `akdeniz27/xlm-roberta-base-turkish-ner` + `savasy/bert-base-turkish-ner-cased` | İki model; span'leri birleştirilir |
| `en`, `ar`, `es`, `fr`, `pt`, `hi`, `bn`, `ur`, `vi` | `Davlan/xlm-roberta-base-wikiann-ner` | Tek model |
| `zh`, `ja`, `ko`, `de`, `it`, `nl`, `pl`, `ru` | `Davlan/bert-base-multilingual-cased-ner-hrl` | Çince, Japonca, Korece ve Rusça'da recall sınırlıdır |
| `th` | `Davlan/xlm-roberta-base-wikiann-ner` | Tayca NER fiilen yoktur (aşağıya bakın) |
| Diğer diller | `Davlan/bert-base-multilingual-cased-ner-hrl` | Yedek model |

**Uzun metinler.** Varsayılan modeller tek geçişte en fazla 512 token okur; bu sayı özel token'lar dahildir, yani yaklaşık 510 token metin demektir. Daha uzun metinler örtüşen pencerelere bölünür; her pencere taranır ve sonuçlar tek bir konum kümesinde birleştirilir. Regex ve Presidio katmanları metnin tamamını her zaman tarar.

**İsteğe bağlı Çince model.** `gyr66/Ernie-3.0-base-chinese-finetuned-ner` modeli, `zh` için Ayarlar → NER Modelleri bölümünden seçilebilir. Hiçbir zaman varsayılan değildir. Yayıncısı Hugging Face Hub'da lisans beyan etmez; etkinleştirmeden önce bu modeli kullanma hakkınızı kendiniz doğrulayın.

**Zayıf kapsam.** Tayca NER fiilen yoktur: Tayca isimler ve yerler esas olarak regex ve Presidio katmanlarıyla yakalanır. Çince, Japonca, Korece ve Rusça'da NER recall'u sınırlıdır. Varsayılan Çince model, Septum'un kendi Çince test dokümanlarında daha az isim ve yeri maskesiz bıraktığı için seçilmiştir; kamuya açık WikiANN Çince setinde ise başka bir model daha yüksek skor alır. Bu diller için NER katmanına tek başına güvenmeyin. Onay mekanizması açıksa metni, buluta gönderilmeden önce orada gözden geçirin.

<p align="center">
  <a href="./index.md"><strong>🏠 Ana Sayfa</strong></a>
  &nbsp;·&nbsp;
  <a href="installation.md"><strong>🚀 Kurulum</strong></a>
  &nbsp;·&nbsp;
  <a href="benchmark.md"><strong>📈 Benchmark</strong></a>
  &nbsp;·&nbsp;
  <a href="features.md"><strong>✨ Özellikler</strong></a>
  &nbsp;·&nbsp;
  <a href="architecture.md"><strong>🏗️ Mimari</strong></a>
  &nbsp;·&nbsp;
  <strong>📊 Doküman İşleme</strong>
  &nbsp;·&nbsp;
  <a href="screenshots.md"><strong>📸 Ekran Görüntüleri</strong></a>
</p>
