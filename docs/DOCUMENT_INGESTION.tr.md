# Doküman İşleme Akışı

<p align="center">
  <a href="../README.tr.md"><strong>🏠 Ana Sayfa</strong></a>
  &nbsp;·&nbsp;
  <a href="FEATURES.tr.md"><strong>✨ Özellikler</strong></a>
  &nbsp;·&nbsp;
  <a href="ARCHITECTURE.tr.md"><strong>🏗️ Mimari</strong></a>
  &nbsp;·&nbsp;
  <strong>📊 Doküman İşleme</strong>
  &nbsp;·&nbsp;
  <a href="SCREENSHOTS.tr.md"><strong>📸 Ekran Görüntüleri</strong></a>
  &nbsp;·&nbsp;
  <a href="../CONTRIBUTING.tr.md"><strong>🤝 Katkı</strong></a>
  &nbsp;·&nbsp;
  <a href="../CHANGELOG.md"><strong>📝 Changelog</strong></a>
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
   - **NER** — `PERSON_NAME`, `LOCATION`, `ORGANIZATION_NAME` için XLM-RoBERTa transformer.
   - **Ollama** — desenlerin yakalayamadığı, bağlama bağlı PII için opsiyonel semantik katman. Varsayılan olarak kapalıdır; açmak için `SEPTUM_USE_OLLAMA=true`.

   Üç detektörün bulduğu çakışan span'ler birleştirilir; coreference çözümlenerek "Ahmet" ile "Bay Yılmaz" aynı placeholder numarasına bağlanır.

5. **Maskeleme ve anonimleştirme haritası** — Tespit edilen her varlık, tipe göre numaralanmış tutarlı bir placeholder ile değiştirilir (`[PERSON_1]`, `[EMAIL_ADDRESS_3]`). `original → placeholder` haritası her doküman için ayrı tutulur, diskte şifreli saklanır ve air-gapped bölgeden asla dışarı çıkmaz.

6. **Paralel işleme** — Maskeli metin üretildikten sonra iki akış eşzamanlı koşar:
   - **Chunking → Embedding → FAISS + BM25** — Maskeli metin, paragraf sınırlarına sadık kalarak ve aralarında örtüşme bırakılarak chunk'lara bölünür. Her chunk sentence-transformers ile embed edilir; sonuçlar hem FAISS vektör indeksine hem BM25 keyword indeksine yazılır.
   - **Şifreli saklama** — Orijinal dosya AES-256-GCM ile mühürlenerek diske yazılır; air-gapped bölge dışında asla deşifre edilmez.

7. **Arama hazır** — FAISS, BM25 ve şifreli dosya yazımı tamamlandığında doküman `ingestion_status="completed"` olarak işaretlenir ve sohbet üzerinden sorgulanabilir hale gelir.

<p align="center">
  <a href="../README.tr.md"><strong>🏠 Ana Sayfa</strong></a>
  &nbsp;·&nbsp;
  <a href="FEATURES.tr.md"><strong>✨ Özellikler</strong></a>
  &nbsp;·&nbsp;
  <a href="ARCHITECTURE.tr.md"><strong>🏗️ Mimari</strong></a>
  &nbsp;·&nbsp;
  <strong>📊 Doküman İşleme</strong>
  &nbsp;·&nbsp;
  <a href="SCREENSHOTS.tr.md"><strong>📸 Ekran Görüntüleri</strong></a>
  &nbsp;·&nbsp;
  <a href="../CONTRIBUTING.tr.md"><strong>🤝 Katkı</strong></a>
  &nbsp;·&nbsp;
  <a href="../CHANGELOG.md"><strong>📝 Changelog</strong></a>
</p>
