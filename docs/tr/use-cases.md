# Septum — Kullanım Senaryoları

Ekiplerin Septum'u gerçekte hangi noktalara yerleştirdiğini gösteren bölüm. Her senaryo iş probleminden başlar, verinin Septum boyunca uçtan uca izlediği yolu adım adım gösterir ve dağıtımı haklı kılan uyumluluk ya da verimlilik kazancını net biçimde söyler.

## Hukuk — ölçekli sözleşme analizi

**Sorun.** Bir hukuk bürosunun elinde binlerce müvekkil sözleşmesi vardır. Ekibi, bir LLM'in bu sözleşmelerdeki ortak fesih hükümlerini, fiyatlama desenlerini ve veri-işleme taahhütlerini gün yüzüne çıkarmasını ister. Ne var ki sözleşmeler müvekkil isimleri, adresler, anlaşma değerleri ve kişisel kimlik numaralarıyla doludur — müvekkil sırrı ve GDPR / KVKK yükümlülükleri altında bunların kamuya açık bir modele iletilmesi mümkün değildir.

**Septum yolu.**

1. PDF'ler panel üzerinden toplu yüklenir. Septum'un ingest hattı her dosyanın dilini tespit eder, büronun aktif tuttuğu regülasyon paketlerini (varsayılan olarak GDPR + KVKK) uygular ve anonimleştirilmiş parçaları orijinalin şifreli kopyasıyla birlikte indeksler.
2. Bir hukukçu sorar: *"Bu sözleşmelerde hangi fesih hükümleri en sık karşımıza çıkıyor?"*
3. Septum soruyu maskeler (burada PII yok) ve maskeli parça indeksi üzerinde hibrit retrieval çalıştırır. Eşleşen paragraflar zaten `[PERSON_1]`, `[ORGANIZATION_NAME_3]`, `[ADDRESS_2]` gibi placeholder'lar taşır.
4. Onay mekanizması maskelenmiş prompt'u ve gönderilecek parçaları yan yana gösterir. Hukukçu onaylar.
5. Bulut LLM cevabı placeholder'larla döner. Septum gerçek varlıkları görüntülemeden önce yerelde geri yazar.
6. Audit modülü kaydı düşer: hangi kullanıcı, hangi dokümanlar, her tipten kaç PII örneği maskelendi, hangi regülasyon paketleri aktifti. GDPR Madde 30 işleme kaydı için fazlasıyla yeterli.

**Neden iş görür.**
- Müvekkil sırrı kod gözden geçirmesinde duvara alınmıştır: ham metnin gateway bölgesine ulaşma yolu yoktur.
- Uyumluluk ekibi, içeriğinde sıfır PII bulunan ayağa kalkmış bir denetim kaydına sahip olur — mevcut SIEM'lerine güvenle gönderebilir.
- Hukukçu, üst sınıf bir LLM'in akıcı sohbet yeteneğini hiç kaybetmez.

## İK — performans ve yetenek analitiği

**Sorun.** İK departmanı; performans değerlendirme döngülerini özetlemek, performans eğilimlerini işaretlemek ve 360-derece geri bildirim mektupları hazırlamak için bir LLM kullanmak ister. Ne var ki temel veri — isimler, maaşlar, kimlik numaraları, ev adresleri, yönetici yorumları — KVKK / GDPR uyarınca makinenin dışına çıkmaması gereken verinin ta kendisidir.

**Septum yolu.** Yapı hukuk senaryosuyla aynıdır: değerlendirme PDF'leri ve elektronik tabloları yüklenir, analitik soru sorulur, maskelemeyi onay mekanizmasında görür, kabul edilir; cevap, tek bir kişiyi ifşa etmeden eğilimi koruyarak geri döner.

Tipik bir sorgu: *"Mühendislik organizasyonunun H1 değerlendirmelerinde yüksek performanslı çalışanlarda en sık görülen davranışsal temalar nelerdir?"* LLM yalnızca `[PERSON_*]` placeholder'larını görür, gerçek isimleri değil; cevap geri yazıldığında gerçek isimler yerelde yerine konur.

**Neden iş görür.** İK analitiği iki yıldır "bunu bir LLM'e gönderebilir miyiz?" sorusunun arkasına sıkışmış durumdaydı. Septum bu sorunun cevabını "evet, maskeli halini" yapar — hem de İK ekibinin her sorgu için özel bir maskeleme hattı yazmasına gerek kalmadan.

## Sağlık — klinik not özetleme

**Sorun.** Bir hastane, çıkış notlarını özetlemek ve uzun bir hasta geçmişini ilgilenen hekim için yoğunlaştırmak amacıyla LLM kullanmak ister. HIPAA, BAA imzalanmamış bir bulut LLM'ine PHI (Korunan Sağlık Bilgisi) gönderilmesini yasaklar. Ancak bu bağlamda LLM özetlemesinin değeri tartışılmaz biçimde yüksektir.

**Semantik katman açıkken Septum yolu.**

1. Hasta kayıtları yüklenir — kağıt formların OCR'lı taramaları dahil.
2. Layer 3 (Ollama semantik tespit) etkinleştirilir; böylece hat yalnız yapısal PHI'yi (isim, MRN, tarih) değil, örüntü eşleştirmenin ifade edemeyeceği semantik kategorileri de yakalar: `DIAGNOSIS`, `MEDICATION`, `BIOMETRIC_ID`, `CLINICAL_NOTE`.
3. İlgilenen hekim sorar: *"Bu hastanın son üç yıldaki diyabet yönetimini özetler misin?"*
4. Maskelenmiş prompt ve parçalar bulut LLM'e iletilir. Model şunu görür: `"[PERSON_1], ilk olarak [DATE_1]'de [DIAGNOSIS_1] tanısı aldı ve [MEDICATION_2] kullanmaya başladı…"` — klinik açıdan anlamlı yapı, sıfır PHI.
5. De-anonimleştirilmiş özet hekimin hasta görünümünde belirir.

**Neden iş görür.** HIPAA denetimleri teknik korumalara önem verir (`§ 164.312`) — Septum bunları sağlar: dinlenmedeki şifreleme, denetim kontrolleri, erişim kontrolleri, iletim güvenliği. Semantik katman sayesinde LLM hastanın kim olduğunu öğrenmeden teşhisler ve ilaçlar üzerinde mantık yürütmeye devam edebilir.

## Serbest sohbet — yazdığınız metni de maskelemek

**Sorun.** Pazarlama yöneticisi, kişiselleştirilmiş bir e-posta hazırlamak için içine gerçek müşteri verisi — isim, e-posta, telefon, adres — yazdığı bir prompt gönderir. Doküman yüklenmemiştir; PII doğrudan yazılmıştır. Pazardaki "gizlilik geçitlerinin" çoğu yalnız dokümanları maskeler ve bu yolu tamamen kaçırır.

**Septum yolu.**

1. Kullanıcı sohbete yazar: *"Ahmet Yılmaz (ahmet@firma.com, TC: 12345678901, adres: İstanbul Caddesi No:42) için hoş geldin e-postası taslağı yaz."*
2. Septum, PII tespitini yalnız yüklenen dokümanlarda değil, **mesajın kendisi üzerinde** de çalıştırır. `PERSON_NAME`, `EMAIL_ADDRESS`, `NATIONAL_ID` (TCKN, mod-10 + mod-11 checksum'u ile doğrulanmış), `POSTAL_ADDRESS` tespit edilir.
3. Bulut LLM'in gördüğü:
   `"[PERSON_1] ([EMAIL_ADDRESS_1], TC: [NATIONAL_ID_1], adres: [POSTAL_ADDRESS_1]) için hoş geldin e-postası taslağı yaz."`
4. LLM bu placeholder'larla kusursuz akıcı bir taslak yazar.
5. Septum gerçek değerleri yerelde geri yazar; kullanıcı, gönderime hazır biçimde gerçek müşteri ismini taşıyan nihai e-postayı görür.

**Neden iş görür.** Septum'un varoluş sebebi "ham PII makineden çıkmaz" cümlesidir — bu cümle, PII'nin bir doküman üzerinden mi yoksa kullanıcının az önce yazdığı bir cümleden mi geldiğine bakmaksızın doğru olmak zorundadır. Pek çok uyumluluk olayı tam da müşteri verisini bir sohbet penceresine yapıştıran biriyle başlar. Septum bu açığı kapatır.

## Geliştirici entegrasyonları — MCP

**Sorun.** Bir geliştirici Claude Code, Cursor ya da Windsurf'ü gün boyu kullanır. Kod tabanı gerçek müşteri test verileri, gerçek dahili endpoint URL'leri ve `.env.example` dosyalarında saklı kalmış gerçek API anahtarları içerir. LLM yardımı almak ister ama bu değerlerin Anthropic / OpenAI'a ulaşmasını istemez.

**MCP üzerinden Septum yolu.**

1. `septum-mcp` bir kez kurulur (`pip install septum-mcp` ya da `uvx septum-mcp`).
2. Editörün MCP yapılandırmasına eklenir:
   ```json
   {
     "mcpServers": {
       "septum": { "command": "septum-mcp", "env": { "SEPTUM_REGULATIONS": "gdpr,kvkk" } }
     }
   }
   ```
3. MCP sunucusu editöre altı araç sunar: `mask_text`, `unmask_response`, `detect_pii`, `scan_file`, `list_regulations`, `get_session_map`.
4. Editör artık herhangi bir snippet'i bulut LLM'e göndermeden önce `mask_text` ile maskeleyebilir, gelen cevabı `unmask_response` ile geri yazabilir. Geliştirici akışını korur; air-gapped invariant onun masası başına gelir.

**Neden iş görür.** MCP açık bir protokoldür; entegrasyon onu konuşan her aracın işine yarar (Claude Code, Cursor, Windsurf, Zed, Cline, Continue, ChatGPT Desktop ve resmi Python / TypeScript / Rust / Go / C# / Java SDK'ları). Gizliliği eklemek tek bir konfig bloğudur; editör yamasına ya da özel eklentilere gerek yoktur.

Tam MCP sunucu referansı (taşımalar, dağıtım, ortam değişkenleri) için [MCP rehberine](https://github.com/byerlikaya/Septum/blob/main/packages/mcp/readme.md) bakın.

---

Yukarıdaki senaryolar Septum'un tasarlandığı somut yapılardır; altta yatan mekanizma her durumda aynıdır — üç katmanlı tespit hattı ve onay mekanizması, farklı regülasyon kümelerine göre yapılandırılmış halde. Her adımın nasıl çalıştığını derinlemesine [Akışlar](workflows) sayfasında, air-gapped garantisini ayakta tutan güvenlik bölgelerini ise [Mimari](architecture) sayfasında bulabilirsiniz.
