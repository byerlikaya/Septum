# Septum — Akışlar

Septum'u günlük kullanım sırasında hareketli parçalarının nasıl bir araya geldiğini gösteren rehber. Her bölüm "şu nasıl çalışır?" sorusunu aynı şablonla yanıtlar: önce bir diyagram, ardından adım adım anlatım, sonunda da pratik dengeleri ve uyarıları.

## Sohbet akışı

Septum'un en kritik kullanıcı yüzeyi. Kullanıcı, içinde PII bulunsun ya da bulunmasın bir mesaj yazar; sistem mesajı yerelde maskeler, eşleşen doküman parçalarını (onlar da maskeli) getirir, insan denetimi için durur, bulut LLM'i placeholder'larla çağırır ve cevabı kullanıcıya göstermeden önce gerçek değerleri yine yerelde geri yazar.

<p align="center">
  <a href="#sohbet-akışı"><img src="../assets/how-it-works.tr.svg" alt="Septum sohbet akışı — kullanıcı sorusu, yerel PII maskeleme, bulut LLM, geri yazılmış cevap" width="820" /></a>
</p>

Adım adım:

1. **Mesajınızı yazın.** Sohbet girişi serbest metin alır; isim, e-posta, kimlik numarası ya da kurumsal bir e-postanın tamamını önceden temizlemek zorunda değilsiniz.
2. **Mesaj üzerinde yerel PII tespiti.** Yüklenen dokümanlarda çalışan üç katmanlı hat (Presidio + NER + opsiyonel Ollama) *daha hiçbir şey olmadan önce* mesajı tarar. Tespit edilen her varlık deterministik bir placeholder'a dönüşür.
3. **Otomatik RAG yönlendirmesi.** Doküman seçimi yapılmamışsa yerel Ollama sınıflandırıcısı, sorunun bir doküman bağlamına ihtiyaç duyup duymadığına (otomatik RAG) ya da düz bir sohbet yanıtının yeterli olduğuna (düz LLM) karar verir. Ayrıntı için [Features sayfasındaki Auto-RAG bölümü](features#otomatik-rag-yönlendirme).
4. **Hibrit retrieval.** Otomatik RAG devreye girdiğinde BM25 anahtar kelime araması ile FAISS semantik araması paralel çalışır; sonuçlar Reciprocal Rank Fusion ile birleştirilir. Çekilen parçalar, diskte zaten maskeli halde duran parçalardır — air-gapped bölgeden ham metin hiçbir aşamada çıkmaz.
5. **Onay mekanizması.** Üç panel açılır: maskelenmiş prompt'unuz, getirilen parçalar ve buluta gidecek hazır istek. Onaylar ya da reddedersiniz — aşağıdaki [Onay mekanizması](#onay-mekanizması) bölümüne bakın.
6. **Bulut LLM çağrısı.** Maskeli prompt, internet-facing bölgedeki `septum-gateway` tarafından seçtiğiniz sağlayıcıya iletilir: Anthropic, OpenAI, OpenRouter ya da yerel Ollama.
7. **Yerel de-anonimleştirme.** Cevap placeholder'larla geri döner; air-gapped bölgedeki `septum-core`, doküman başına tutulan anonimleştirme haritasından gerçek değerleri yerine koyar. Düz PII yalnızca bellekte yeniden oluşturulur — ne kuyruğa ne gateway sürecine değer.
8. **Denetim kaydı.** Bir uyumluluk olayı yazılır: kim sordu, hangi dokümanlara dokunuldu, hangi tipten kaç varlık maskelendi. Olay kaydı ham PII içermez.

Aynı maskeleme hattı sadece getirilen parçalar üzerinde değil, kullanıcının yazdığı mesaj üzerinde de çalışır. Yazdığınız `"Ahmet'e ahmet@firma.com adresinden ulaşalım"` ifadesi, retrieval başlamadan önce `"[PERSON_1]'e [EMAIL_ADDRESS_1] adresinden ulaşalım"` haline gelir. Bulut sağlayıcılarının orijinali görmemesi gerçek anlamıyla bir garantidir.

## Onay mekanizması

Güvenlik ağı. Air-gapped bölgeyi terk etmeden önce gönderilecek olanın ne olduğunu görür ve isteği tek bir tıkla durdurabilirsiniz.

<p align="center">
  <a href="#onay-mekanizması"><img src="../assets/approval-gate-flow.tr.svg" alt="Onay mekanizması — üç panel (maskelenmiş prompt, getirilen parçalar, hazır bulut isteği) ve gönder/reddet kararı" width="900" /></a>
</p>

Üç panel:

| Panel | Ne gösterir | Ne yapabilirsiniz |
|---|---|---|
| **Maskelenmiş prompt** | PII placeholder'a dönüştükten sonraki mesajınız | Burada salt-okunur. Metni değiştirmek için sohbet girdisini düzenleyip yeniden gönderin. |
| **Getirilen parçalar** | LLM'in göreceği doküman bölümleri — onlar da maskeli | Düzenlenebilir. İstemediğiniz parçayı çıkarın, ilgisiz kısımları kırpın, bağlamı sıkı tutun. |
| **Buluta gidecek tam istek** | Host'u terk edecek olan bayt bayt nihai içerik | Salt-okunur. Son uçuş öncesi kontrol — burada ters bir şey görüyorsanız reddedin ve düzeltin. |

Karar:

- **Onayla** → istek `septum-queue` üzerinden `septum-gateway`'e gider, gateway bulut LLM'i çağırır ve maskelenmiş cevabı geri yayınlar. De-anonimleştirme cevap görüntülenmeden önce yerelde tamamlanır.
- **Reddet** → istek olduğu yerde düşer. Hiçbir şey makineden çıkmaz. Sohbet girdisi yazdığınız metni korur, böylece düzeltip tekrar deneyebilirsiniz.

Otomatik tespit riski azaltır; onay mekanizması ise riski tümüyle ortadan kaldırır. Bir katman yanılsa bile (örneğin nadir bir kimlik formatında yanlış negatif), giden payload'ın tamamını gözünüzle görüp duraklatabilirsiniz.

Mekanizmayı istek bazında, oturum bazında ya da küresel olarak Ayarlar'dan kapatabilirsiniz; tespitin sertleştirildiği yüksek hacimli otomasyonlar için kullanışlı olur. Varsayılan "her zaman açık"tır; çünkü beklenmedik tek bir sızıntının maliyeti, fazladan bir tıklama maliyetinin çok üzerindedir.

## Özel kurallar

Hazır regülasyon paketleri yaygın PII şekillerini (GDPR, KVKK, HIPAA gibi mevzuatların tanımladığı kategoriler) zaten kapsar; ne var ki her kurumun ders kitabı regex'ine uymayan en az bir tanımlayıcısı vardır — `EMP-2024-00041`, `PROJ-X-gizli`, dahili kod adları gibi. Özel kurallar, Septum'a bunları öğretmek için üç araç sunar.

<p align="center">
  <a href="#özel-kurallar"><img src="../assets/custom-rules-flow.tr.svg" alt="Özel kurallar — üç tespit tipi (regex, anahtar kelime, LLM promptu), her biri policy composer'a entegre" width="880" /></a>
</p>

| Tip | En uygun durum | Maliyet | Örnek |
|---|---|---|---|
| **Regex** | Sabit şekilli yapısal tanımlayıcılar | Çok düşük | Dahili çalışan kimlikleri `EMP-\d{4}-\d{5}`, talep numaraları, proje kodları |
| **Anahtar kelime** | Bire bir maskelenmesi istenen kapalı sözlük | Çok düşük | Kod adları, dahili müşteri rumuzları, hassas ürün isimleri |
| **LLM promptu** | Hiçbir regex'in ifade edemeyeceği semantik kategoriler | Parça başına bir Ollama çağrısı | "Bu paragrafta duyurulmamış bir ürün geçiyor mu?", "Klinik bir teşhis var mı?" |

Arayüzden ekleme adımları:

1. **Ayarlar → Regülasyonlar → Özel kurallar → Yeni kural.**
2. **Bir isim** verin — bu isim, denetim olaylarında varlık tipi olarak görünür.
3. **Bir tip seçin.** Form, tipe göre kendini düzenler — regex alanı, anahtar kelime metin kutusu ya da prompt şablonu.
4. **Test edin.** Örnek bir doküman ya da cümleyi yapıştırın, "Dene"ye basın; eşleşen alanlar anlık olarak vurgulanır.
5. **Kaydedin.** Kural sıradaki istekte aktif politikaya katılır. Hazır paketler ile özel kurallarınız policy composer üzerinden birleşir; çakışmada her zaman en kısıtlayıcı kural geçerli olur.

Kuralları regülasyon başına kapsam sınırlandırılabilir — "bu kural yalnızca KVKK aktifken çalışır" — ya da küresel bırakılabilir. Aynı ekrandan düzenlenir, devre dışı bırakılır veya silinir; sunucu yeniden başlatmaya gerek yok, politika kendiliğinden tazelenir.

## Denetim kaydı

Her tespit bir satır yazar. Her bulut LLM çağrısı bir satır daha. İkisi birlikte uyumluluk yükümlülüklerine birebir karşılık gelen ekleme-yalnız bir defter oluşturur (GDPR Madde 30 kayıtları, KVKK Veri Sorumlusu kayıtları, HIPAA erişim logları).

Kayda neler düşer:

- **Olay tipi** (`document.ingested`, `pii.detected`, `chat.approved`, `chat.rejected`, `llm.forwarded`, `llm.responded`)
- **Kaynak modül** (`septum-api`, `septum-gateway`, `septum-audit`)
- **Correlation id** — tek bir sohbet turunu modüller arasında bağlar
- **Tip bazında varlık sayıları** — varlığın değeri değil, yalnızca sayısı
- **Olay anındaki aktif regülasyon kimlikleri**
- **Bulut çağrıları için gecikme, model, sağlayıcı**
- **Kullanıcı id, oturum id** — kim, hangi oturumda

Kayda neler düşmez: ham PII'nin kendisi, doküman metni, prompt içeriği ya da LLM cevabı. Denetim olayları sözleşme gereği PII'siz tutulur; üçüncü taraf bir SIEM'e güvenle gönderilebilmesinin sebebi tam olarak budur.

Kayıtla yapılabilecekler:

- **Panel üzerinden filtreleme.** Regülasyon, varlık tipi, kullanıcı, tarih aralığı bazında. Arayüz, tespit sayılarındaki sıçramaları öne çıkarır; alışılmadık bir hareket bir bakışta fark edilir.
- **Varlıklara odaklan.** Herhangi bir denetim satırından "Varlıklara odaklan" düğmesiyle ilgili dokümanı yalnız o olayın tespitleri vurgulanmış halde açın — bir dakika içinde ne işaretlendiğini ve neden işaretlendiğini görürsünüz.
- **Dışa aktarma.** `GET /api/audit/export?format=json|csv|splunk` ile ilgili dilim dışarı çekilir. Splunk HEC payload'ları doğrudan toplayıcıya gönderilebilir; JSON özel işleme hatları için, CSV ad-hoc incelemelerde Excel açılışı için uygundur.
- **Saklama politikası.** Yaş ve sayı olmak üzere iki kapasite kuralı bulunan kayan pencere. Yerinde, atomik yeniden yazım — okuma tarafında kesinti olmaz.

Audit modülü tasarım gereği **internet-facing bölgede** koşar — SIEM ile konuşması gerekir ama ham PII'yi asla görmemelidir. Olaylar üreticide (`septum-api`) zaten temizlendiği için audit host'u tehlikeye düşse bile sıfır kişisel veri sızar.

---

Yukarıdaki akışlar Septum'un hareket halini anlatır. Özellik kataloğu ve tespit katmanları için [Özellikler](features) sayfasına, sistem mimarisi ve modül sınırları için [Mimari](architecture) sayfasına bakın.
