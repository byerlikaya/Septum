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
   - **Presidio** — regex desenleri ve algoritmik validator'lar (TCKN checksum, Aadhaar Verhoeff, NRIC/FIN, CPF, NINO, My Number ve diğerleri; CNPJ yalnızca biçimine göre eşleşir).
   - **NER** — `PERSON_NAME`, `LOCATION`, `ORGANIZATION_NAME` için dil grubuna göre seçilen transformer modeli. Kapsam dillere göre değişir; bkz. [NER model kapsamı](#ner-model-kapsamı).
   - **Ollama** — desenlerin yakalayamadığı, bağlama bağlı PII için opsiyonel semantik katman. Yeni kurulumda yalnızca sihirbaz çalışan bir yerel modelle tamamlandığında açılır ([İlk açılış](installation#i̇lk-açılış--kurulum-sihirbazı) bölümüne bakın); aksi halde kapalıdır. Ayarlar → Gizlilik ve Anonimleştirme → *Ollama semantik tespit* seçeneğinden değiştirilir. `USE_OLLAMA_SEMANTIC_LAYER_DEFAULT` ayar kaydı oluşturulurken bir kez okunur: `true` katmanı açar, `false` kapalı tutar. Ayar açıkken yerel modelin doküman metninin her dilimi için yanıt vermesi gerekir (dilim en fazla 2.000 karakterdir, komşu dilimler 200 karakter paylaşır); yanıt veremezse doküman işleme başarısız olur. Ayar açıkken işlenmiş dokümanlar yeniden maskeleme için işaretlenir.

   Çakışan span'lerde hiçbir tespit edilmiş karakter maskesiz kalmaz: öncelikli span bütün olarak korunur (e-posta, telefon, IBAN ve kimlik numaraları gibi yapılandırılmış tanımlayıcılar önce gelir), diğer span ise kapsamadığı kısmı kendi tipiyle maskelenir. Aynı tipteki çakışan span'ler tek bir span'e dönüşür. Adres katmanları en dıştakini önce çözer (önce posta adresi, sonra sokak adresi, en son konum); kısmen bir kurum veya adres span'inin dışında kalan konum ise yalnızca dışarıda kalan kısmı için maskelenir. Kalan kısım kazananın kendi kelimesinin devamıysa, örneğin "Ayşe Kaya'nın" içindeki "'nın" ise, ayrı bir placeholder olmaz; kazananın placeholder'ına katılır. Harita ek almamış hâli ("Ayşe Kaya") kaydeder; sonraki ek almamış anmalar aynı placeholder'ı alır ve placeholder geri çözüldüğünde ek almamış değer geri gelir. Ad anmaları token eşleşmesiyle bağlanır: "Elif Demir" tespit edildikten sonra, dokümanda başka hiçbir kişide olmayan "Elif" ya da "Demir" aynı placeholder numarasını kullanır; ancak "Hanım Demir" bağlanmaz, çünkü unvan bir ad token'ı sayılır.

5. **Maskeleme ve anonimleştirme haritası** — Tespit edilen her varlık, tipe göre numaralanmış tutarlı bir placeholder ile değiştirilir (`[PERSON_NAME_1]`, `[EMAIL_ADDRESS_3]`). `original → placeholder` haritası her doküman için ayrı tutulur, diskte şifreli saklanır ve air-gapped bölgeden asla dışarı çıkmaz.

6. **Chunking, saklama ve indeksleme** — Maskelemeden sonra bu sırayla yürür:
   - **Chunking** — Tespit tüm doküman üzerinde zaten tek seferde yapıldığı için chunk'lar sonradan, dokümanın kendi yapısına (sayfalar, başlıklar, paragraflar, tablolar, hesap tablosu satırları, ses segmentleri) göre kesilir. Kesim önce blok sınırını, sonra cümle sonunu, sonra boşluğu tercih eder ve tespit edilmiş bir değerin ortasına asla düşmez. Metin chunk'ları varsayılan olarak PDF ve DOCX'te 1.200, diğer formatlarda 800 karaktere kadar doldurulur; boyutu aşan bir blok örtüşmeyle (varsayılan 200 karakter) bölünür. Hesap tabloları satır sınırlarından (varsayılan 200 hücre), ses dosyaları süreye göre (varsayılan 60 saniye) kesilir. Her chunk kendi sayfa numarasını, çalışma sayfası (sheet) numarasını veya zaman damgalarını taşır. Maskelemeden sonra bir chunk hâlâ 20.000 karakteri aşıyorsa doküman `chunk_too_large` koduyla başarısız olur ve hiçbir şey kaydedilmez.
   - **Metin boyutu sınırı** — kişisel veri tespitinden önce, normalleştirilmiş çıkarılan metnin karakterleri sayılır (bayt değil, karakter). `MAX_EXTRACTED_TEXT_CHARS` (varsayılan 10.000.000) aşılırsa doküman `extracted_text_too_large` koduyla reddedilir: hiçbir şey maskelenmez, chunk'lanmaz ya da indekslenmez, metin kesilmez ve doküman başarısız görünür. Şifreli özgün dosya, doküman silinene kadar durur. Sınırı aşan hâle gelmiş bir dokümanı yeniden işlemek aynı şekilde başarısız olur ve önceki chunk'larını, tespitlerini, indekslerini ve önizleme metnini siler; `chunk_too_large` da böyle davranır. Sınır bellek ve süre içindir: 10 milyon karakterde, varsayılan modellerle tespit test makinesinde (bir masaüstü) yaklaşık 3,5 GB bellek zirvesine ulaştı ve yaklaşık 4 ila 5,5 dakika sürdü. Yüklü modeller yüzünden 4 GB'lık bir sunucu, sınır ne olursa olsun dardır. Bkz. [Yükleme ve ses değişkenleri](installation.md#yükleme-ve-ses-değişkenleri). Özel kural Test'inin taşıyabileceği metin ayrıdır: en fazla 20.000 karakter.
   - **Chunk saklama** — Her chunk maskeli ve orijinal metniyle saklanır; dokümanın transkripsiyon metni de saklanır. Sohbet, bulut modele yalnızca maskeli metni gönderir; arama, yerel görüntüleme ve geri yükleme orijinal metin üzerinde yerelde çalışır. Orijinal chunk metni ve transkripsiyon metni veritabanında şifrelenmeden durur; dosya, indeksler ve anonimleştirme haritası ise şifrelidir. Bu yüzden veritabanının ve diskinin korunması (disk şifreleme, erişim denetimi) kurulumu yapanın sorumluluğundadır.
   - **Embedding → FAISS + BM25** — Her chunk sentence-transformers ile embed edilir; sonuçlar önce FAISS vektör indeksine, ardından BM25 keyword indeksine yazılır. İki indeks de maskeli metinden değil, chunk'ın orijinal metninden kurulur ve air-gapped bölgede kalır.

   Orijinal dosya ayrı bir adım değildir: yukarıdaki adımların hiçbirinden önce, yükleme sırasında AES-256-GCM ile mühürlenip diske yazılır ve yalnızca API süreci içinde deşifre edilir: ingestion için ve oturum açmış bir kullanıcı kendi dokümanının önizlemesini açtığında (admin herhangi birini açabilir); bu durumda deşifre edilmiş dosya o kullanıcının tarayıcısına sunulur. Bir doküman silindiğinde dosyası, chunk satırları (orijinal metin dahil), FAISS ve BM25 indeksleri ve anonimleştirme haritası da silinir.

7. **Arama hazır** — Anonimleştirme haritası ve iki indeks yazıldığında doküman `ingestion_status="completed"` olarak işaretlenir ve sohbet üzerinden sorgulanabilir hale gelir.

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

**Zayıf kapsam.** Tayca NER fiilen yoktur: Tayca isimler ve yerler esas olarak regex ve Presidio katmanlarıyla yakalanır. Çince, Japonca, Korece ve Rusça'da NER recall'u sınırlıdır. Varsayılan Çince model, Septum'un kendi Çince test dokümanlarında daha az isim ve yeri maskesiz bıraktığı için seçilmiştir; kamuya açık WikiANN Çince setinde ise başka bir model daha yüksek skor alır. Çince ve Japonca'da tespit edilen bir isim veya yer, çevresindeki cümle parçasını da kapsamak yerine karakter karakter tam olarak maskelenir. Tayca'da recall çok düşük olduğundan, bir Tayca NER isabeti çevresindeki boşluksuz diziyi bilerek bütünüyle maskeler; böylece modelin aynı dizide kaçırdığı isimler de kapsanır. Bu fazla maskeleme kasıtlıdır ve yalnızca Tayca olarak tespit edilen dokümanlarda değil, her dokümandaki Tayca metinde geçerlidir. Çince, Japonca veya Tayca metnin hemen yanına yazılmış bir e-posta adresi de tespit edilir. Bu diller için NER katmanına tek başına güvenmeyin. Onay mekanizması açıksa metni, buluta gönderilmeden önce orada gözden geçirin.

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
