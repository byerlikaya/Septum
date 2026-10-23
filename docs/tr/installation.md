# Septum — Kurulum Rehberi

<p align="center">
  <a href="./index.md"><strong>🏠 Ana Sayfa</strong></a>
  &nbsp;·&nbsp;
  <strong>🚀 Kurulum</strong>
  &nbsp;·&nbsp;
  <a href="benchmark.md"><strong>📈 Benchmark</strong></a>
  &nbsp;·&nbsp;
  <a href="features.md"><strong>✨ Özellikler</strong></a>
  &nbsp;·&nbsp;
  <a href="architecture.md"><strong>🏗️ Mimari</strong></a>
  &nbsp;·&nbsp;
  <a href="document-ingestion.md"><strong>📊 Doküman İşleme</strong></a>
  &nbsp;·&nbsp;
  <a href="screenshots.md"><strong>📸 Ekran Görüntüleri</strong></a>
</p>

---

## İçindekiler

- [⚡ Hızlı başlangıç](#-hızlı-başlangıç)
- [Sistem gereksinimleri](#sistem-gereksinimleri)
- [Kurulum varyantları](#kurulum-varyantları)
  - [Tam yerel yığın (önerilen)](#1-tam-yerel-yığın--önerilen)
  - [Tek container — demo](#2-tek-container--demo)
  - [Yalnız air-gapped bölge](#3-yalnız-air-gapped-bölge)
  - [Yalnız internet-facing bölge](#4-yalnız-internet-facing-bölge)
  - [Kaynaktan geliştirici kurulumu](#5-kaynaktan-geliştirici-kurulumu)
- [İlk açılış — kurulum sihirbazı](#i̇lk-açılış--kurulum-sihirbazı)
- [Erişim ve ağ ayarları](#erişim-ve-ağ-ayarları)
- [LLM sağlayıcıları](#llm-sağlayıcıları)
- [Veri kalıcılığı ve volume'lar](#veri-kalıcılığı-ve-volumelar)
- [Güncelleme](#güncelleme)
- [Sorun giderme](#sorun-giderme)
- [Kaldırma](#kaldırma)

---

## ⚡ Hızlı başlangıç

Çalışan bir Septum'a giden en kısa yol. Üç komut, beş dakika — paketi içindeki Ollama ile birlikte local-first bir AI ara katmanı.

```bash
git clone https://github.com/byerlikaya/Septum.git && cd Septum
cp .env.example .env
# .env dosyasını editörünüzde açıp POSTGRES_PASSWORD, REDIS_PASSWORD ve dört SEPTUM_QUEUE_*_PASSWORD değerini doldurun
docker compose up --build
```

Compose, boş kalan değişkeni adıyla belirten bir hatayla durur.

Tarayıcınızda **http://localhost:3000** adresini açın. Kurulum sihirbazının ilk adımı, API'nin günlüğüne yazdırılan bir kurulum anahtarı ister: `docker compose logs api`. Sihirbaz gerisini halleder. Bkz. [Kurulum anahtarı](#kurulum-anahtarı).

Compose yığını, tek bir makinede PostgreSQL, Redis, Ollama, FastAPI backend ve Next.js paneli birlikte ayağa kaldırır. Septum kaynaktan kurulur: ilk açılışta Septum image'ları yerelde derlenir (birkaç dakika sürer), temel servis image'ları ve Ollama'nın varsayılan modeli indirilir; sonraki açılışlar derleme önbelleğini kullanır. Ham PII hiçbir zaman makineden dışarı çıkmaz — tespit, maskeleme ve LLM çıkarımının tamamı yerelde yürür.

---

## Sistem gereksinimleri

| Kaynak | Asgari | Önerilen |
|---|---|---|
| CPU | 2 çekirdek (x86-64 veya arm64) | 4 çekirdek ve üzeri |
| RAM | 6 GB boş | 16 GB (Ollama'nın 7B-sınıfı model tutması için) |
| Disk | 12 GB boş | 30 GB (model + doküman indeksleri zamanla büyür) |
| Docker | Desktop 4.30+ / Engine 24+ (Compose v2 dahil) | Son kararlı sürüm |
| OS | macOS 13+, WSL2 ile Windows 10/11, güncel bir Linux dağıtımı | — |

**Platform notları.**

- **Apple Silicon (M1/M2/M3/M4)** — tam destekli. Ollama Metal hızlandırmasını kendiliğinden devreye alır; ek bir bayrağa ihtiyacınız yoktur. Image'lar arm64 için yerel olarak derlenir.
- **NVIDIA GPU (Linux amd64)** — opsiyoneldir. CUDA hızlandırmalı PyTorch için (OCR, Whisper ve embedding ölçülebilir biçimde hızlanır) `docker/standalone.Dockerfile` veya `docker/api.Dockerfile` image'ını `TORCH_VARIANT=gpu` build argümanıyla derleyin (compose dosyasında servisin `build.args` bölümüne `TORCH_VARIANT: gpu` ekleyip `up --build` çalıştırın; ya da `docker build -f docker/api.Dockerfile --build-arg TORCH_VARIANT=gpu -t septum-api:local .` ile kendiniz derleyip yığını `--build` olmadan başlatın, böylece compose bu etiketi korur). Host'ta CUDA 13 desteği olan bir NVIDIA sürücüsü gerekir. Ollama, container'dan bir GPU gördüğünde bu cihazı otomatik kullanır.
- **Windows** — WSL2 gereklidir; native Windows container'ları desteklenmez. Rehber boyunca görülen yollar ve volume örnekleri POSIX kabuğuna göredir; komutları WSL terminalinden çalıştırın.

---

## Kurulum varyantları

Desteklenen beş topoloji vardır. Dağıtım şeklinize uyan varyantı seçin.

### 1. Tam yerel yığın — **önerilen**

Tek bir host üzerinde tüm modüllerin yanı sıra Ollama, PostgreSQL ve Redis. Varsayılan `docker-compose.yml` bu topolojiyi üretir. Bu bir geliştirme yığınıdır: tüm servisler internet erişimi olan tek bir ağı paylaşır, bu yüzden bölge izolasyonu zorlanmaz. Bölgeler arasında ayrım için 3. ve 4. varyantlardaki air-gapped ve internete açık dosyaları kullanın. Bu yığında tek bir Redis hem kuyruğu hem de hız sınırı ve kurulum belirteci anahtarlarını `noeviction` politikasıyla tutar. Bir akış birikimi (örneğin audit worker'ı çalışmıyorsa) bu Redis'i doldurabilir; dolduğunda giriş denemeleri reddedilir. Bölünmüş topolojilerde bunlar ayrı Redis örneklerinde tutulur. Kuyruğun boyutunu `SEPTUM_QUEUE_REDIS_MAXMEMORY` ile ayarlayın. Bu Redis kalıcılık olmadan çalışır ve veri volume'u yoktur; bu yüzden yeniden başladığında hız sınırı ve kurulum anahtarı deneme sayaçları sıfırlanır.

```bash
git clone https://github.com/byerlikaya/Septum.git && cd Septum
cp .env.example .env
# .env dosyasını açın ve POSTGRES_PASSWORD, REDIS_PASSWORD ve dört SEPTUM_QUEUE_*_PASSWORD değerini doldurun (zorunlu)

docker compose up --build       # ön planda — ilk açılış loglarını izleyin
# ya da
docker compose up -d --build    # arka planda — terminali serbest bırakır

# İlk açılışta paketli Ollama'ya varsayılan bir model çekin
docker compose exec ollama ollama pull llama3.2:3b
```

**http://localhost:3000** adresine gidin. Sihirbazın akışı için [İlk açılış](#i̇lk-açılış--kurulum-sihirbazı) bölümüne bakın.

İsteğe bağlı `mcp` servisi (profil `mcp`) models volume'unu salt okunur bağlar; bu yüzden yalnızca volume'da zaten bulunan ve hash denetimini geçen modelleri yükler. `api` henüz yüklemediği diller için `mcp`'ye güvenmeden önce `api`'yi ısıtın ya da fetch komutunu çalıştırın (`docker compose run --rm api python -m septum_api.cli.models fetch`).

Ollama olmaksızın çalıştırmak isterseniz (yalnız bulut sağlayıcı kullanılan dağıtımlar):

```bash
docker compose -f docker-compose.yml -f docker-compose.no-ollama.yml up --build
```

### 2. Tek container — **demo**

Tek bir container, SQLite, harici servis yok. Bir dizüstü bilgisayarda Septum'u hızlıca denemenin en pratik yolu; satış demoları ve küçük ölçekli kurulumlar için idealdir.

```bash
git clone https://github.com/byerlikaya/Septum.git && cd Septum
docker compose -f docker-compose.standalone.yml up -d --build
```

Kurulum anahtarı container günlüğüne yazdırılır: `docker compose -f docker-compose.standalone.yml logs septum`.

Standalone varyantı Ollama'yı paketinde taşımaz. Ya host'a Ollama kurun (`brew install ollama && ollama serve`) ve sihirbaza `http://host.docker.internal:11434` adresini verin, ya da Ollama'yı atlayıp bulut sağlayıcısıyla (Anthropic / OpenAI / OpenRouter) ilerleyin.

**Standalone'u şu durumlarda seçin**: mümkün olan en hızlı "hemen deneyeyim" akışını istiyorsanız, korpusunuz küçükse ve modüler bölge ayrımına ihtiyaç duymuyorsanız.

**Şu durumlarda seçmeyin**: air-gap ayrımı, çok kullanıcılı ölçek ya da Septum'un tüm özellik setine (semantik PII katmanı + Otomatik RAG yönlendirme — her ikisi de Ollama ister) ihtiyacınız varsa.

### 3. Yalnız air-gapped bölge

Yalnızca air-gapped bölgeyi çalıştırır: api, web, PostgreSQL, Redis, Ollama ve profili etkinse mcp. Hepsi `zone` ağındadır; bu ağ internal'dır. Docker Desktop üzerinde internete giden bir rotası yoktur ve bu ağda yayımlanan bir port host'tan erişilemez (aşağıdaki notlara bakın). `zone` ile normal bridge'in ikisinde birden bulunan tek container `zone-edge`'dir; tek bir haproxy relay'idir ve bölgeye giriş ya da çıkış için tek yoldur:

- `:3000` host'tan panele (web).
- `:8765` host'tan mcp'ye; mcp profili etkinse (`SEPTUM_MCP_BIND` ile bağlanır).
- `:6379` api'den, `SEPTUM_GATEWAY_QUEUE_ADDR` adresindeki gateway bölgesi Redis'ine.
Ollama bölgenin içinde `ollama` servisi olarak çalışır; `http://ollama:11434` adıyla erişilir. Yayımlanmış portu ve dışarı çıkış rotası yoktur; bu yüzden paneldeki model indirmeleri ve `*-cloud` model etiketleri orada başarısız olur. Modeller, `zone` üzerinde olmayan tek seferlik `ollama-pull` servisiyle yüklenir:

```bash
docker compose -f docker-compose.airgap.yml run --rm ollama-pull llama3.2:3b
```

Tek seferlik çalıştırma, `edge` üzerinde geçici bir Ollama başlatır, adı verilen modelleri paylaşılan `ollama-data` volume'una çeker ve çıkar. İnternete erişimi olmayan bir host'ta bunun yerine modelleri bağlantılı bir host'tan bu volume'a kopyalayın. `<project>`, Compose proje adıdır (`Septum` adlı bir dizindeki kaynak ağacı için `septum`):

```bash
docker run --rm -v <project>_ollama-data:/dst -v "$HOME/.ollama":/src:ro alpine cp -a /src/models /dst/
```

`zone` üzerindeki container'ların dışarı çıkış rotası yoktur. Bu, Docker Desktop üzerinde doğrulanmıştır. `zone` ağı `internal`dır; normal bridge üzerinde de bulunan tek container `zone-edge`'dir. Relay yalnızca yukarıdaki yolları taşır; kuyruk yolu yalnızca api'nin sabit adresinden (`SEPTUM_API_IP`) bağlantı kabul eder. Relay trafik ve istemci adreslerini loglamaz; yalnızca başlangıç ve yapılandırma bildirimlerini yazar; bunlar Docker log sürücüsüne gider (`docker compose -f docker-compose.airgap.yml logs zone-edge`).

Native Linux Docker Engine ile test edilmemiştir. Internal bir ağda hâlâ host üzerinde bir gateway adresi bulunur; bu yüzden `0.0.0.0`'a bağlı bir host servisine zone container'larından erişilebilir. Host servislerini loopback'e ya da tek bir belirli arayüze bağlayın.

```bash
cp .env.example .env
# POSTGRES_PASSWORD, REDIS_PASSWORD ve SEPTUM_QUEUE_API_PASSWORD değerlerini doldurun (gateway host'unda kullanılanla aynı değer)
# SEPTUM_GATEWAY_QUEUE_ADDR değerini <gateway-özel-ip>:6379 olarak ayarlayın

docker compose -f docker-compose.airgap.yml up -d --build
```

`SEPTUM_GATEWAY_QUEUE_ADDR` ve `SEPTUM_QUEUE_API_PASSWORD` olmadan compose başlamaz. Dosya `USE_GATEWAY_DEFAULT=true` kullanır; bunun iki etkisi vardır. Bir kuyruk yapılandırılmamışsa api başlamayı reddeder. Ayrıca gateway yönlendirmesini zorlar: değişken true iken her bulut LLM çağrısı, saklanan `use_gateway` ayarı ne derse desin kuyruktan geçer. Saklanan ayar değişken tarafından değiştirilmez. Her bulut LLM çağrısı maskelenmiş bir istek olarak gateway host'unun Redis'ine yayımlanır ve oradan yanıt alınır; api, yalnızca istek ve yanıt akışlarına erişebilen `septum-api` ACL kullanıcısıyla bağlanır. Docker Desktop üzerinde internal `zone` ağı, doğrudan bir bulut çağrısına çıkış rotası vermez; bu yüzden kuyruk tek yoldur (yukarıdaki notlara bakın). Ollama ile yerel yanıtlar yönlendirilmez ve eskisi gibi çalışır. `REDIS_URL` bölgenin kendi Redis'inde kalır; bu Redis hız sınırlayıcıyı, kurulum anahtarını ve hata günlüğü kovalarını tutar; bölgeden hiç çıkmaz. Kuyruktan yalnızca maskelenmiş metin geçer.

**İnternetsiz host için modelleri hazırlama.** Air-gapped host'un internet erişimi yoktur; bu yüzden model önbelleği bağlantılı bir makinede doldurulur ve host'a kopyalanır. Sabitlemeler (pin) kodun içinde olduğu için iki makine de aynı Septum kaynak ağacını çalıştırmalıdır. Bağlantılı makinede, bu kaynak ağacından:

```bash
docker compose -f docker-compose.airgap.yml build api
docker run --rm -v septum_septum-models:/app/models septum-api:local python -m septum_api.cli.models fetch --whisper base
docker run --rm -v septum_septum-models:/src -v "$PWD":/dst alpine tar czf /dst/septum-models.tar.gz -C /src .
```

İndirmeyi `docker compose run` ile değil, `docker run` ile çalıştırın. `compose run`, çıkış rotası olmayan internal `zone` ağına katılır; indirme orada başarısız olur.

`fetch` komutu, varsayılan her modeli sabitlenmiş commit'inden indirip doğrular; ardından `--whisper` ile verilen Whisper modelini indirir (kullandığınız her boyut için seçeneği tekrarlayın). `--whisper` verilmezse `WHISPER_MODEL` ayarındaki model indirilir; bu ayarın varsayılanı `base`'dir. Son komut model volume'unu arşivler; volume adı compose proje önekini taşır (`Septum` adlı bir dizindeki kaynak ağacı için `septum_`). `docker run` ile yapılan fetch compose dosyasını okumaz; bu yüzden image'ın varsayılan, çevrimiçi modu geçerlidir. Bu, indirme yapan tek adımdır ve bağlantılı host üzerinde çalışır. `septum-models.tar.gz` dosyasını air-gapped host'a kopyalayın, ardından:

```bash
docker compose -f docker-compose.airgap.yml build api
docker compose -f docker-compose.airgap.yml create
docker run --rm -v septum_septum-models:/dst -v "$PWD":/src alpine tar xzf /src/septum-models.tar.gz -C /dst
docker compose -f docker-compose.airgap.yml up -d
docker compose -f docker-compose.airgap.yml run --rm api python -m septum_api.cli.models fetch --whisper base --offline-check
```

`--offline-check` kopyalanan dosyaları ağa çıkmadan doğrular. Her model için bir satır yazdırır; herhangi bir dosya eksik ya da sabitlemesiyle uyuşmuyorsa çıkış durumu 1 olur.

Her iki host'ta `alpine` image'ının yerelde bulunması gerekir: bağlantılı makinede `docker pull alpine` çalıştırın ve image'ı `docker save` ile `docker load` üzerinden aktarın. İçinde `tar` bulunan herhangi bir image, yukarıdaki komutlarda onun yerine kullanılabilir. Air-gapped compose dosyası `api` ve `mcp` servislerinde `HF_HUB_OFFLINE=1` ayarlar; bu container'lar model indirmez. Etkinleştirildiğinde `mcp` servisi models volume'unu salt okunur (read-only) bağlar; bu yüzden yalnızca volume'da zaten bulunan ve hash denetimini geçen modelleri yükler. `api`'nin henüz yüklemediği diller için `mcp`'ye güvenmeden önce volume'u `fetch` komutuyla (tüm varsayılan sabitlemeler) doldurun.

**Özel NER modelleri.** Özel model, bağlantılı host'ta `--user-pin` kullanılarak çekilir. Her özel model için seçeneği tekrarlayın:

```bash
docker run --rm -v septum_septum-data:/app/data -v septum_septum-models:/app/models septum-api:local python -m septum_api.cli.models fetch --user-pin org/name@<40 karakterlik commit SHA>
docker run --rm -v septum_septum-data:/src -v "$PWD":/dst alpine cp /src/septum-user-pins.json /dst/
```

User-pins dosyası `config.json` yanında duran `septum-user-pins.json` dosyasıdır; bu yüzden models volume'unda değil, `septum-data` volume'unda bulunur. Dosyayı models arşivi ile birlikte air-gapped host'a kopyalayın. Yukarıda anlatılan `create` adımından sonra, `up -d` öncesinde dosyayı data volume'una geri yükleyin:

```bash
docker run --rm -v septum_septum-data:/dst -v "$PWD":/src alpine cp /src/septum-user-pins.json /dst/
```

### 4. Yalnız internet-facing bölge

Yalnızca gateway ve audit modüllerini, bulut LLM sağlayıcılarına ulaşabilen bir host üzerinde çalıştırır. Redis'ten maskelenmiş istekleri tüketir ve Anthropic / OpenAI / OpenRouter'a iletir.

```bash
cp .env.example .env
# Dört kuyruk parolasını doldurun: SEPTUM_QUEUE_API_PASSWORD, SEPTUM_QUEUE_GATEWAY_PASSWORD,
#   SEPTUM_QUEUE_AUDIT_PASSWORD, SEPTUM_QUEUE_HEALTH_PASSWORD
# SEPTUM_GATEWAY_REDIS_BIND değerini bu host'un özel veya VPN adresine ayarlayın (zorunlu; bu olmadan compose başlamaz)
# Sağlayıcı anahtarlarını girin: ANTHROPIC_API_KEY, OPENAI_API_KEY vb.

docker compose -f docker-compose.gateway.yml up -d --build
```

Bu dosya, kuyruk olan Redis'i; gateway'i, onun sağlık uç noktasını ve audit servisini çalıştırır. Bu host'ta `REDIS_PASSWORD` kullanılmaz ve Redis'in `default` kullanıcısı kapalıdır. Her istemcinin kendi ACL kullanıcısı vardır (bkz. [Kuyruk Redis kullanıcıları](#kuyruk-redis-kullanıcıları)).

Gateway image'ı güvenlik invariantı gereği `septum-core`'u içermez — yalnızca placeholder ile maskelenmiş metni görür ve maskelenmiş cevapları geri yayınlar. Audit sidecar'ı her isteği varlık tipi, sayı ve regülasyon id'leriyle loglar (ham PII asla yer almaz) ve `/api/audit/export` uç noktasını SIEM entegrasyonu için açar.

**Air-gapped + internet-facing birlikte**: iki Docker host. Kuyruk, gateway host'unun `SEPTUM_GATEWAY_REDIS_BIND` üzerinde yayımlanan Redis'idir. Air-gapped host'ta `SEPTUM_GATEWAY_QUEUE_ADDR` değerini `<bu adres>:6379` olarak ayarlayın ve yalnızca `SEPTUM_QUEUE_API_PASSWORD` değerini kopyalayın. Gateway, audit veya sağlık parolalarını asla air-gapped host'a kopyalamayın. Redis bağlantısı düz metindir (AUTH ve maskelenmiş payload'lar) ve bu dosya bir TLS dinleyicisi yapılandırmaz; bu yüzden yalnızca VPN veya özel bağlantı üzerinden çalıştırın. TLS, Redis dinleyicisinde sonlandırıldığında `SEPTUM_QUEUE_URL` içinde `rediss://` çalışır. İstemci sertifikayı ve URL'deki host adını doğrular; bu yüzden sertifika bu host adını içermelidir (air-gapped varsayılanı için `zone-edge`).

### 5. Kaynaktan geliştirici kurulumu

Geliştirme, hata ayıklama veya özel build'ler için. Üretim ortamı için önerilmez; bunun yerine bir compose varyantı kullanın.

```bash
git clone https://github.com/byerlikaya/Septum.git && cd Septum
./dev.sh --setup        # altı Python paketini ve npm bağımlılıklarını kurar
./dev.sh                # api ile web'i port 3000 üzerinde başlatır
```

Gereksinimler: Python 3.11+ (3.13 üzerinde doğrulandı), Node.js 22+ (Docker image'ları ve CI Node 26 ile derlenir), ffmpeg (Whisper ses akışı için). Bootstrap script'i ilk açılışta yeni bir şifreleme anahtarı ve JWT gizli anahtarıyla `config.json` üretir; yolu değiştirmek için `SEPTUM_CONFIG_PATH` ortam değişkenini kullanabilirsiniz. Açılışta arka planda yapılan model ön yüklemesini atlamak için `SEPTUM_DISABLE_MODEL_WARMUP=1` ayarlayın; varsayılanda NER, spaCy ve embedding modelleri ön yüklenir. Ön yükleme kapalıyken, saklı model ayarlarıyla ilgili başlangıç uyarısı da atlanır. `SEPTUM_USER_PINS_PATH`, özel NER modellerinin hash'lerinin kaydedileceği yeri belirler; varsayılanı `config.json` yanındaki `septum-user-pins.json` dosyasıdır (Docker'da `/app/data`). `mcp` servisi uygulama veri volume'unu bağlamaz; çünkü bu volume `config.json` dosyasını ve şifreleme anahtarını tutar. Bu yüzden `mcp` user-pins dosyasını oradan okuyamaz ve kullanıcı sabitlemeli özel NER modellerini kutudan çıktığı gibi kullanamaz. `mcp`'de birini kullanmak için user-pins dosyasının bir kopyasını salt okunur bir bağlama koyun ve `SEPTUM_USER_PINS_PATH` değişkenini bu kopyaya yöneltin.

**Model sabitlemelerini güncelleme (bakımcılar).** `python packages/api/scripts/pin_models.py --write-manifest` her varsayılan modelin Hugging Face Hub üzerindeki `main` commit'ini okur ve `packages/core/septum_core/model_pins.py` dosyasını yeniden üretir; commit'lemeden önce farkı gözden geçirin. `--check` her sabitlenmiş commit'i yeniden okur ve sapma olursa çıkış durumu 1 ile sonlanır. İkisi de ağ erişimi gerektirir.

Test setini çalıştırın:

```bash
pytest packages/*/tests -q        # 7 modülün tamamı
cd packages/web && npm test       # frontend Jest suite
```

---

## İlk açılış — kurulum sihirbazı

Compose varyantlarından biri açılışı tamamladığında **http://localhost:3000** adresine gidin. Sihirbaz ilk kurulumda bir kez çalışır ve backend'in ihtiyaç duyduğu her şeyi yapılandırır. Sihirbazın ilk adımı kurulum anahtarını ister.

| Adım | Ne yaparsınız | Notlar |
|---|---|---|
| **1. Veritabanı** | SQLite (varsayılan) veya PostgreSQL seçin, bağlantıyı test edin | Compose varyantları PostgreSQL'i hazır getirir; standalone SQLite'a düşer |
| **2. Önbellek** | In-memory (varsayılan) veya Redis seçin, bağlantıyı test edin | Compose Redis'i hazır getirir; standalone in-memory kullanır |
| **3. LLM sağlayıcı** | Ollama (yerel, tam yığında paketli) / Anthropic / OpenAI / OpenRouter | Anahtar edinimi için [LLM sağlayıcıları](#llm-sağlayıcıları) bölümüne bakın |
| **4. Regülasyonlar** | İhtiyaç duyduğunuz paketleri (genelde GDPR + kendi ülkeniz) seçin | Daha sonra Ayarlar → Regülasyonlar sekmesinden değiştirilebilir |
| **5. Ses modeli** | Whisper boyutu (tiny / base / small / medium / large) | Ses dosyası yüklemeyecekseniz atlayın; ilk ses yüklemesinde tembel indirilir |
| **6. Admin hesabı** | İlk yönetici için e-posta ve şifre | Tek admin burada oluşturulur; diğer hesaplar panelden eklenir |

Sihirbaz tamamlandığında `config.json` yazılır, admin kullanıcı oluşturulur ve panele yönlendirilirsiniz. Sihirbaz tek seferlik bir akıştır; kurulum bittikten sonra `/api/setup/*` uçlarına yapılan istek 403 döner.

### Sihirbaza yazılacak tam değerler (tam compose yığını)

`docker compose up` ile tam yığını başlattıysanız, api container'ı paketli PostgreSQL, Redis ve Ollama'ya docker ağı üzerinden ulaşır — `localhost` üzerinden değil. Host adı olarak compose servis isimlerini kullanın:

| Adım | Alan | Buraya yapıştırın (`<…>` yerine `.env` değerinizi yazın) |
|---|---|---|
| **1. Veritabanı → PostgreSQL** | URL | `postgresql+asyncpg://septum:<POSTGRES_PASSWORD>@postgres:5432/septum` |
| **2. Önbellek → Redis** | URL | `redis://:<REDIS_PASSWORD>@redis:6379/0` |
| **3. LLM sağlayıcı → Ollama (paketli)** | URL | `http://ollama:11434` |

`<POSTGRES_PASSWORD>` / `<REDIS_PASSWORD>` hızlı başlangıçta `.env` dosyasında belirlediğiniz değerlerdir. Unutulmuşsa `grep -E 'POSTGRES_PASSWORD\|REDIS_PASSWORD' .env` ile çıkarın. Redis URL'inde `://` ile `:` arasında kullanıcı adı yoktur — bu Redis'in örtük `default` kullanıcısıdır; şifreden önceki iki nokta üst üste zorunludur.

**Kendi Redis'inizi mi kullanıyorsunuz?** Redis'i `maxmemory-policy noeviction` ile çalıştırın (Redis'in varsayılanı; paketli compose Redis'i de bunu kullanır). Bir doküman silindiğinde Redis'e kısa ömürlü bir tombstone yazılır; bu kayıt diğer worker'ların o dokümanın anonimleştirme haritasının önbellekteki kopyasını sunmasını engeller. Herhangi bir eviction politikası — tombstone bir TTL taşıdığı için `allkeys-*` de `volatile-*` de — onu vaktinden önce silebilir.

**Host'ta zaten bir Ollama çalışıyorsa:** port çakışmasını önlemek için paketli olanı durdurun (`docker compose stop ollama && docker compose rm -f ollama`), ardından Ollama URL'i olarak `http://host.docker.internal:11434` yazın. docker-compose.yml api container'ına `extra_hosts` ile `host.docker.internal`'ı bağlamış durumda; host'taki Ollama'ya ek bir ayar olmadan erişilir. Air-gapped dosyası bunu yapmaz: ordaki api yalnızca bölge içindeki `ollama` servisine (`http://ollama:11434`) erişir ve orada `host.docker.internal` çözümlenmez.

**Ollama URL kuralı.** Septum bir Ollama URL'ini yalnızca host'u loopback (`127.0.0.0/8`, `::1`), `localhost`, `host.docker.internal`, `host.containers.internal` veya `ollama` (paketli compose servisi) olduğunda kabul eder. Bunların dışındaki her host reddedilir ve HTTP 400 (`ollama_url_not_allowed`) döner; buna LAN veya özel IP adresleri, `.local` adları ve uzak sunucular dahildir; `https://` üzerinden bile olsa. Çünkü Ollama maskelenmemiş metin alır ve ham PII makineden çıkmamalıdır. `OLLAMA_BASE_URL` ilk kurulumda ayarlara kopyalanır ve kayıtlı URL yokken kullanılır; bundan sonra kayıtlı URL geçerlidir. Aynı kural bu değişken için de geçerlidir: reddedilen değer varsayılana düşmez, bu nedenle düzeltilene kadar Ollama çağrıları reddedilir. Ollama için özel bir compose servis adı kabul edilmez. `ollama`, `host.docker.internal` ve `host.containers.internal` adları isimleriyle güvenilir kabul edilir; bu adları `/etc/hosts`, DNS ya da container ağında başka bir makineye yönlendirmeyin. Septum adı kontrol eder, adın nereye çözüldüğünü değil. Daha önce kaydedilmiş bir URL artık reddediliyorsa Ollama özellikleri kapalı kalır ve ayarlar sayfası, URL değiştirilene kadar bunu URL alanının altında işaretler.

Her adımın **Bağlantıyı Test Et** butonu api container'ının içinden çalışır — yani backend kurulum sonrasında ne görecekse sihirbaz o anda onu gösterir. Yeşil onay = devam edebilirsiniz.

---

## Erişim ve ağ ayarları

### Kurulum anahtarı

Kurulum açıkken API ilk açılışta bir kurulum anahtarı (setup token) üretir ve her açılışta günlüğüne yazdırır:

```
Septum setup token: <token>
```

Anahtarı `docker compose logs api` ile (tam yığın), `docker compose -f docker-compose.airgap.yml logs api` ile (air-gapped dosyası), `docker compose -f docker-compose.standalone.yml logs septum` ile (standalone) ya da kaynaktan kurulumda `./dev.sh` çalıştıran terminalden okuyun. Sihirbaz bunu ilk adımda ister; sonraki her kurulum isteği anahtarı gönderir. Anahtar, ilk yönetici hesabı oluşturulduğunda silinir. O zamana kadar container günlüğünü veya `config.json` dosyasını (veri volume'u) okuyabilen herkes kurulumu tamamlayabilir; bu nedenle günlüğü ve veri volume'unu hassas kabul edin.

Değeri kendiniz seçmek için `SEPTUM_SETUP_TOKEN` değişkenini en az 32 karakter ve en az 10 farklı karakterden oluşacak şekilde ayarlayın. Septum daha kısa ya da daha az çeşitli bir değerle başlamayı reddeder. `openssl rand -hex 32` ile üretebilirsiniz. Değişken ayarlıysa günlük değeri yazdırmaz; `Septum setup is open; use the token set in SEPTUM_SETUP_TOKEN.` satırını yazar.

Zaten bir kullanıcısı olan bir kurulumda açık bir kurulum penceresi yoktur; bu yüzden anahtar üretilmez ve yazdırılmaz.

### Güvenilen proxy'ler

API, bir istemcinin adresini `X-Forwarded-For` başlığından yalnızca istek listelenen bir proxy'den geldiğinde okur. Panel (Next.js) bu proxy'dir ve başlığı kendisi ayarlar. Bu nedenle `FORWARDED_ALLOW_IPS` yalnızca panelin adresini listeler:

- Standalone: `127.0.0.1` (iki süreç de aynı container'ı paylaşır).
- Compose: web container'ının sabit adresi, `SEPTUM_WEB_IP` (varsayılan `172.28.70.10`). Compose dosyaları bunu sizin için ayarlar.

`FORWARDED_ALLOW_IPS` hiçbir zaman `*` kabul etmez; `/8` (IPv4) ya da `/32` (IPv6) değerinden geniş hiçbir aralığı kabul etmez.

Panelin önünde ters bir proxy varsa `SEPTUM_TRUSTED_PROXIES` değişkenini panel sürecinde (web container'ının ortamında ya da `npm start` çalıştıran host'ta) proxy'nin adresi ya da CIDR'ı olarak ayarlayın; aynı boyut sınırları geçerlidir. Panel, istemci adresini proxy'nin `X-Forwarded-For` başlığından okur. Geçersiz bir girdide başlamayı reddeder. Tam yığın bu değişkeni ayarlamaz; bu durumda `web` servisinin ortamına ekleyin. Air-gapped dosyası, `zone-edge` relay'i panelin önünde durduğu ve gerçek istemci adresini `X-Forwarded-For` olarak ayarladığı için bu değişkeni relay'in sabit adresine (`SEPTUM_ZONE_EDGE_IP`, varsayılan `172.28.70.11`) ayarlar.

### Yayımlanan portlar

Yalnızca panel (port 3000) tüm arayüzlerde dinler. Diğer yayımlanan her port, kendi bind değişkeni ayarlanmadıkça `127.0.0.1` adresine bağlanır; gateway Redis'in varsayılanı yoktur ve `SEPTUM_GATEWAY_REDIS_BIND` gerektirir:

| Port | Servis | Varsayılan bind | Değişken |
|---|---|---|---|
| 3000 | Panel (web; air-gapped dosyasında zone-edge relay) | tüm arayüzler | — |
| 8000 | API (yalnızca tam yığın) | `127.0.0.1` | — (standalone'da ve air-gapped dosyasında yayımlanmaz) |
| 5432 | PostgreSQL (yalnızca tam yığın) | `127.0.0.1` | — |
| 11434 | Ollama (yalnızca tam yığın) | `127.0.0.1` | `SEPTUM_OLLAMA_BIND` |
| 8765 | MCP sunucusu (`mcp` profili) | `127.0.0.1` | `SEPTUM_MCP_BIND` |
| 8002 | Audit API | `127.0.0.1` | `SEPTUM_AUDIT_BIND` |
| 8001 | Gateway sağlık | `127.0.0.1` | `SEPTUM_GATEWAY_HEALTH_BIND` |
| 6379 | Gateway Redis (gateway dosyası) | zorunlu | `SEPTUM_GATEWAY_REDIS_BIND` |

Air-gapped dosyası ne Redis, ne PostgreSQL ne de api portu yayımlar. Bu servislerde komut çalıştırmak için `docker compose -f docker-compose.airgap.yml exec <servis> ...` kullanın. Tam yığın Redis'i yayımlamaz. Docker'ın yayımladığı portlar ufw ve firewalld'nin INPUT kurallarını atlar. Yukarıdaki bind değişkenlerini kullanın; gerektiğinde DOCKER-USER kuralları ekleyin.

### Kuyruk Redis kullanıcıları

Gateway kuyruğu, ACL'li bir Redis'tir. `default` kullanıcısı kapalıdır ve her istemci kendi kullanıcısıyla giriş yapar; bu kullanıcı yalnızca kendi akışlarına ve yalnızca akış komutlarına erişebilir:

| Kullanıcı | Erişebildiği akışlar | Compose dosyası |
|---|---|---|
| `septum-api` | istek ve yanıt akışları | air-gapped (zone-edge üzerinden), gateway, tam yığın |
| `septum-gateway` | istek, yanıt ve audit akışları | gateway, tam yığın |
| `septum-audit` | audit akışı | gateway, tam yığın |
| `health` | yalnızca `PING` | gateway, tam yığın (sağlık kontrolü) |

Her kullanıcının parolası `.env` dosyasındadır: `SEPTUM_QUEUE_API_PASSWORD`, `SEPTUM_QUEUE_GATEWAY_PASSWORD`, `SEPTUM_QUEUE_AUDIT_PASSWORD` ve `SEPTUM_QUEUE_HEALTH_PASSWORD`. `openssl rand -hex 32` ile üretin; yalnızca hex kullanın, çünkü Redis'in `--user` argümanları boşluk ya da tırnak taşıyamaz. `requirepass` gibi bu parolalar da container üzerinde `docker inspect` çalıştırabilen herkes tarafından görülebilir; bu nedenle Docker host'una erişimi kısıtlayın.

### Gateway kuyruğu değişkenleri

| Değişken | Ayarlandığı yer | Anlamı |
|---|---|---|
| `SEPTUM_GATEWAY_QUEUE_ADDR` | air-gapped host (`.env`) | `<gateway-özel-ip>:6379`. zone-edge relay, api'nin kuyruk bağlantısını buraya iletir. Air-gapped dosyası için zorunludur. |
| `SEPTUM_QUEUE_URL` | api | Kuyruk URL'i. Air-gapped varsayılanı: `redis://septum-api:<SEPTUM_QUEUE_API_PASSWORD>@zone-edge:6379/0`; tam yığın varsayılanı: aynı kullanıcı, `redis:6379` üzerinde. Air-gapped host'ta ayarlamak varsayılanın yerini alır; zone-edge'e işaret etmeye devam etmelidir. |
| `SEPTUM_QUEUE_DIR` | api, gateway | `SEPTUM_QUEUE_URL` yerine dosya tabanlı bir kuyruk dizini. Compose dosyaları Redis kullanır. |
| `SEPTUM_ZONE_EDGE_IP` | air-gapped host (`.env`) | `zone` ağındaki zone-edge'in sabit adresi; varsayılan `172.28.70.11`. Panel bunu proxy olarak güvenir. |
| `SEPTUM_GATEWAY_REQUEST_TOPIC` / `SEPTUM_GATEWAY_RESPONSE_TOPIC` | api, gateway | Akış adları. Varsayılanlar `septum.llm.requests` ve `septum.llm.responses`. İki taraf eşleşmelidir; compose dosyaları bunları ayarlar. |
| `SEPTUM_GATEWAY_CLIENT_TIMEOUT_SECONDS` | api | api'nin bir gateway yanıtını ne kadar beklediği; bu süre, en fazla 25 sn'lik bloklayan okumalarla sınıra ulaşılana kadar tekrarlanarak geçer. Varsayılan `120`. Pozitif bir sayı olmalıdır; aksi halde kuyruk yapılandırılmışken api başlamayı reddeder. |
| `SEPTUM_GATEWAY_TIMEOUT_SECONDS` | gateway | Tek bir upstream denemesi için toplam süre sınırı; yalnızca bağlantıyı değil, isteğin tamamını kapsar. Varsayılan `30`. |
| `SEPTUM_GATEWAY_MAX_ATTEMPTS` | gateway | İstek başına deneme sayısı. Varsayılan `3`. |
| `SEPTUM_QUEUE_REDIS_MAXMEMORY` | gateway host'u (`.env`), tam yığın | Kuyruk Redis'inin bellek üst sınırı; Redis boyut sözdiziminde (örneğin `512mb`). Varsayılan `256mb`. Politika `noeviction`'dır: sınıra ulaşıldığında yeni kuyruk yazmaları başarısız olur, bu yüzden istek kuyruğa alınmaz. Zaten kuyrukta olan hiçbir girdi tahliye edilmez ve hiçbir şey doğrudan bulut sağlayıcısına gönderilmez. Üst sınırı beklenen birikime göre belirleyin. |
| `SEPTUM_API_IP` | air-gapped host (`.env`) | `zone` ağındaki api'nin sabit adresi; varsayılan `172.28.70.12`. zone-edge kuyruğu yalnızca bu adresten iletir. `SEPTUM_SUBNET`, `SEPTUM_DYNAMIC_IP_RANGE`, `SEPTUM_WEB_IP` ve `SEPTUM_ZONE_EDGE_IP` ile birlikte değiştirin. |

api'nin istemci zaman aşımı, gateway'in en kötü süresinin üzerinde kalmalıdır. Varsayılanlarla bu 3 deneme × 30 sn, artı en fazla 10 sn'de sınırlanmış iki `Retry-After` beklemesi, yaklaşık 110 sn eder. Varsayılan istemci zaman aşımı 120 sn bunu karşılar. `SEPTUM_GATEWAY_TIMEOUT_SECONDS` ya da `SEPTUM_GATEWAY_MAX_ATTEMPTS` değerini artırdığınızda `SEPTUM_GATEWAY_CLIENT_TIMEOUT_SECONDS` değerini de buna uygun artırın; yoksa api, gateway'in hâlâ ilettiği yanıtları beklemeden vazgeçer.

**Kuyruk saklaması.** Redis arka ucuyla her kuyruk girdisi işlendikten sonra onaylanır ve silinir (önce XACK, sonra XDEL); böylece kuyrukta yalnızca bekleyen iş kalır. Dosya arka ucuyla işlenen mesajlar `SEPTUM_QUEUE_DIR` altındaki `done/` dizinine, reddedilen ya da bozuk mesajlar `dead-letter/` dizinine taşınır. 16 MiB'den büyük ya da çözümlenemeyen bir girdi (çok derin iç içe JSON ya da ayrıştırıcının basamak sınırını aşan bir tam sayı) aynı şekilde işlenir: dosya arka ucu onu `dead-letter/` dizinine taşır, Redis arka ucu ise onaylayıp siler; yalnızca kimliğini ve nedenini loglar. Dosya arka ucunda `dead-letter/` içindeki dosya adları benzersizdir; önceki kanıtların üzerine hiçbir zaman yazılmaz. Dizin arızası (yazılamayan, eksik ya da dolu kuyruk dizini) çalışanı hatayla durdurur ve mesajları yerinde bırakır; mesajlar `dead-letter/` dizinine taşınmaz. Backend bu dizinlerin hiçbirini kırpmaz; dosya arka ucunu kullanıyorsanız eski dosyaları oradan kendiniz kaldırın. Kuyruk Redis'i kalıcılık kapalı çalışır (`--save ""`, `--appendonly no`); anlık görüntü ya da append-only dosya yazmaz. Kuyruk Redis'i yeniden başlarsa, işlenmekte olan istekler başarısız olur ve çağıran bir hata alır. Kuyruk Redis'i ayrıca `--maxmemory ${SEPTUM_QUEUE_REDIS_MAXMEMORY}` ve `--maxmemory-policy noeviction` ile çalışır. Dolduğunda yeni yazmalar reddedilir ve yeni istekler kapalı biçimde başarısız olur; kuyruktaki mesajlar hiçbir zaman tahliye edilmez. Üst sınırı `SEPTUM_QUEUE_REDIS_MAXMEMORY` ile belirleyin.

**Saat senkronizasyonu.** Bölünmüş dağıtımda (air-gapped ve internete açık host'lar) iki host de saatini NTP ya da kurum içi bir zaman kaynağıyla eşitli tutmalıdır. Birkaç dakikalık bir sapma, api'nin geçerli gateway yanıtlarını eski diye atmasına yol açar; istek zaman aşımına uğrar ve Ollama'ya düşer.

### İzleme

Panel artık `/metrics` isteklerini proxy'lemez. API'yi doğrudan kazıyın: compose ağı içinden `api:8000`, host üzerinden `127.0.0.1:8000`. Standalone container 8000 portunu yayımlamaz; air-gapped dosyası da yayımlamaz. Standalone'da bir compose override'ında `127.0.0.1:8000:8000` ile kendiniz yayımlayın. Air-gapped dosyasında ise bölgenin içinden kazıyın, örneğin `docker compose -f docker-compose.airgap.yml exec api` ile. `/metrics` oturum gerektirmez, yönlendirilebilir bir adrese açmayın. `WEB_CONCURRENCY` 1'den büyükse her kazıma tek bir worker'ın sayaçlarını okur.

Yeni Prometheus serileri:

- `septum_rate_limit_storage_up`: rate-limit depolaması yanıt verdiği sürece 1.
- `septum_unauthenticated_rejections_total`: oturumu ya da API anahtarı olmayan istemcilere verilen 401 ve 403 yanıtları, duruma göre.
- `septum_password_work_refused_total`: parola kuyruğu dolu olduğu için 503 ile reddedilen giriş, kayıt veya parola değiştirme istekleri.
- `septum_error_log_writes_dropped_total`: istemcinin bütçesi tükendiği için yazılmayan hata günlüğü satırları.
- `septum_error_log_write_failures_total`: saklanamayan hata günlüğü satırları.
- `septum_error_log_retention_failures_total`: başarısız olan hata günlüğü saklama çalışmaları; bir sonraki çalışmada yeniden denenir.

`GET /health` ayrıca `rate_limit_storage` alanını döndürür: `ok`, depolama erişilemediğinde `degraded`.

### Hata günlüğü saklama

`ERROR_LOG_RETENTION_DAYS` (varsayılan `90`) eski hata günlüğü satırlarını günde bir kez siler. `0` bunları süresiz tutar ve başlangıçta bir uyarı yazar. Oturumu ya da API anahtarı olmayan bir istemciye verilen 401 veya 403 yanıtları sayılır ama saklanmaz; böylece tabloyu dolduramazlar. Diğer satırlar kullanıcı, API anahtarı ya da istemci adresi başına bir bütçeden düşer: dakikada 60, sunucu hataları için dakikada 20. Bütçeyi aşan satırlar yalnızca sayılır. Satırlar, somut URL yerine route şablonunu kaydeder (örneğin `/api/documents/{document_id}`).

### Denetim olayı saklama

API veritabanındaki denetim olaylarının yaş sınırı yoktur. Bir belge silindiğinde olayları da silinir; bir belgeye bağlı olmayan olaylar kalır. Olaylar varlık türlerini, sayıları, maskelenmiş yer tutucuları ve dil kodlarını, ayrıca ilgili belgenin özgün dosya adını tutar; dosya adı da kişisel veri içerebilir. İşlemi yapan kullanıcıyı kaydeden olaylar (bir dil geçersiz kılma değişikliği kullanıcı kimliğini saklar) o kullanıcı silindiğinde korunur, yalnızca kimlik kaldırılır. Septum bu olayları kendiliğinden budamaz ya da dışa aktarmaz; ilkeniz bir süre sınırı gerektiriyorsa `audit_events` tablosunu kendi takviminizle budayın.

septum-audit servisi kendi JSONL defterini tutar; bu defter yukarıdaki olaylardan ayrıdır. `SEPTUM_AUDIT_RETENTION_DAYS` ve `SEPTUM_AUDIT_RETENTION_MAX_RECORDS` yalnızca bu canlı defter dosyasını budar. Worker başlangıçta, sonra her etkin dönemde bir kez budama yapar. Dönem, bir turun başlangıcından sonrakinin başlangıcına kadar sürer ve `max(SEPTUM_AUDIT_RETENTION_INTERVAL_SECONDS, son turun süresinin 10 katı)` değerine eşittir; aralık varsayılanı `3600`'dür. Ayarlanmayan sınır kayıtları süresiz tutar; bu yüzden politikanız bir sınır gerektiriyorsa bir sınır ayarlayın. 1'in altındaki bir değer worker'ı başlangıçta durdurur. Bir consumer ya da retention görevi beklenmedik biçimde durursa süreç hatayla çıkar ve konteyner yeniden başlatılır.

Neyin kapsandığı ve neyin kapsanmadığı:

- Yalnızca canlı `audit.jsonl` budanır ve yalnızca en eski ardışık kayıtları. Döndürülmüş dosyalar, yedekler ya da anlık görüntüler, `/api/audit/export` ile indirilen dosyalar ve bir SIEM ya da Splunk'a gönderilen kopyalar kendi saklamasına ihtiyaç duyar. Kuyruk depolaması yukarıdaki Kuyruk saklaması bölümünde ele alınır.
- Kayıtlar `received_at` ile yaşlandırılır; bu, sink'in kaydı aldığı zamandır. Sink damgayı basar, hash bunu kapsar ve üretici bunu ayarlayamaz. `received_at` alanından önce yazılmış defterler `timestamp` değerine döner. Kullanılabilir bir yaşı olmayan eski bir satır (eksik, sıfır ya da sonlu olmayan bir `timestamp`), bir sonraki kaydın yaşını alır: o kayıt silindiğinde silinir, sonraki kayıt yoksa tutulur.
- Sınır: bilinmeyen yaşlı eski satırlar hariç (yukarıya bakın), bir kayıt en fazla `SEPTUM_AUDIT_RETENTION_DAYS` artı bir etkin dönem artı bir turun süresi kadar tutulur ve bu yalnızca turlar başarılı oldukça geçerlidir. `halted`, `failed`, `blocked` veya `stale` durumlarında sınır geçerli değildir ve `/health` durumu bildirir.
- Saat sabitlenmesi: yaş sınırı ayarlıysa ve kullanılabilir yaşı bilinen en eski kaydın yaşı saatin 300 saniyeden fazla ilerisindeyse bu kayıt kullanılabilir yaşı bilinen en eski kayıt olduğunda durum `blocked` olur ve budama o kayıtta durur; çünkü bu kayıt asla kesim tarihinden eski değildir ve ondan sonraki kayıtlar saat yetişene kadar budanmaz. Sayı sınırı bir sabitlenmiş defterde de geçerlidir. 300 saniyeye kadar saat sapması işaretlenmez. Nedene göre çözüm şöyledir:
  - İleri saat atlaması: saati düzeltin. Ardından ya gerçek zamanın damgalanan zamanı geçmesini bekleyin (budama kendiliğinden devam eder) ya da defteri ve anchor'ı `JsonlFileSink(path).verify()` komutunun geçtiği bir kopyadan geri yükleyin.
  - Geriye NTP adımı: adımdan sonra damgalanan kayıtlar, bu kayıtlar sona erene kadar daha büyük önceki damgaların gerisinde kalır. Saati düzeltin. Ardından bu kayıtların sona ermesini bekleyin ya da doğrulanmış bir kopyadan geri yükleyip sıfırlamayı kabul edin.
  - Anchor'ı olmayan bir defterin başına eklenmiş, gelecek tarihli bir eski satır: eklenmeden önce alınmış doğrulanmış bir kopyadan geri yükleyin.
- Sayı sınırında histerezis vardır, ancak kesin bir tavan değildir. Defter `SEPTUM_AUDIT_RETENTION_MAX_RECORDS` değerini yüzde 5 (en az bir kayıt) aştığında budanır ve tam olarak sınıra indirilir. İki tur arasında defter, sınırın üzerindeki bu yüzde 5 payı ve bu gecikme sırasında eklenen kayıtlar kadar daha fazla kayıt tutabilir.
- Kayıt silen her budama `audit.jsonl.anchor` dosyasını yazar. Son silinen kaydın sha256 özetini ve bu budamadan önce defterin başladığı özeti (önceki anchor'ın özeti; ilk budamada 64 sıfır), birikimli silinen kayıt sayısını (parçalı bir kalıntı sayılmaz) ve budama zamanını tutar. Defter bu iki özetin herhangi biriyle doğrulanır; bu yüzden anchor yazılıp defter değiştirilmeden önce çökme olursa da doğrulama geçer. Kayıt içeriği hiçbir zaman tutulmaz. Doğrulama anchor'dan başlar, bu yüzden budanmış bir defter yine doğrulanır.
- Hash zinciri anahtarsız SHA-256'dır ve anchor imzasızdır. Defter dizinine yazma erişimi olan biri en eski kayıtları silip eşleşen bir anchor yazabilir ve doğrulama yine geçer. Gerçek kurcalama direnci için defteri ve anchor'ı sunucu dışına, dışa aktarıcı ya da SIEM üzerinden taşıyın. Anahtarlı imzalama uygulanmamıştır.
- Hash zinciri, defterin sonundan kesilmesini (tail truncation) tespit edemez. Aynı şekilde, eski bir defteri eşleşen anchor'ıyla birlikte geri yüklemek de tespit edilmez; en son budamadan hemen önce alınmış bir kopya, mevcut anchor'a karşı yine doğrulanır. Her iki dosyayı da dosya izinleriyle koruyun; daha güçlü bir garanti gerekiyorsa dış bir kopya tutun.
- v1.2.0'dan önce yazılmış defterler `prev_hash` veya `hash` içermeyen satırlarla başlayabilir (v1.0.0 ve v1.1.0 biçimi). Bu başlangıç bölümü yalnızca anchor yokken ya da anchor, `legacy_prefix` işaretli bir genesis anchor iken kabul edilir. Bu satırlar yukarıdaki yaş kuralına göre budanır. `received_at` taşıyan bir hash'siz satır ya da zincirli bir kayıttan sonra gelen herhangi bir hash'siz satır saklamayı durdurur.
- Hiçbir şey içeriği nedeniyle silinmez. `attributes`, hash'lenmeden önce normalleştirilir: anahtarlar metne dönüşür (iç içe anahtarlar dahil, çünkü seri hali onları metne çevirir), iki öğeli çiftlerden oluşan bir liste bir sözlüğe dönüşür, sözlük olmayan her diğer değer `{"value": ...}` içine alınır ve üst düzeyde çakışan anahtarlar `"value"` altında çiftlerden oluşan bir liste olarak tutulur (iç içe bir çakışmada son değer kalır). Seri hale getirilemeyen bir değer `"<unserialisable>"` olur; 32 seviyeden daha derin bir değer `"<depth limit>"` olur. Sonlu olmayan sayılar `NaN`, `Infinity` veya `-Infinity` metinlerine dönüşür. Sıfır, boolean, metin, eksik, aralık dışı ya da sonlu olmayan bir `timestamp` alım zamanıyla değiştirilir ve özgün değer tutulmaz. Bunların her biri kayıt düzeyindeki `ingest_replaced` özniteliğini `true` yapar; bu öznitelik hash tarafından kapsanır ve yalnızca bu kayıtta bir değerin alımda değiştirildiğini söyler, hangi alan olduğunu söylemez. Bu işareti yalnızca sink koyar; sink, yazmadan önce üreticinin kopyasını atar. `ingest_unstorable` için de aynısı geçerlidir; bu işaret, sink'in reddettiği bir kaydın yerine sakladığı yer tutucuyu işaretler. Sink'in kaydı okunduğunda doğrulanacak biçimde saklayamadığı durumda (satır geri okunduğunda doğrulanmazsa) kayıt bir yer tutucu olarak tutulur: id (64 karaktere kesilmiş), source, olay türü ve correlation id korunur, `attributes` `{"value": "<unstorable>"}` olur ve içerik saklanmaz. Yer tutucunun `attributes` alanı ayrıca `ingest_unstorable` ve `ingest_replaced` işaretlerini taşır. Bu normalleştirmeden önce yazılmış ve `NaN` ya da `Infinity` değişmezleri içeren satırlar, onları yazan serileştiriciyle doğrulanmaya devam eder. Consumer, içeriği hiç kayda ayrıştırılamayan bir payload'ı malformed olarak onaylar ve loglar; hiçbir şey yazılmaz. Diğer her hata mesajı yeniden kuyruğa alınır ve yeniden denemeler 2 saniyeden başlayıp her seferinde iki katına çıkar, en çok 60 saniyeye kadar.
- Dışa aktarımların üçünde de (JSON, CSV ve SIEM) sonlu olmayan sayılar metin olarak yazılır; böylece JSON ve SIEM çıktısı katı JSON olarak kalır. Dışa aktarım ortada başarısız olursa hata türü loglanır ve aktarım kesilir; istemci eksik bir gövde görür. İlk kayıttan önce başarısız olan bir dışa aktarım HTTP 500 döner. Sonlu olmayan değerlerin metin olarak yazıldığı dışa aktarım satırları (eski NaN satırları) `verify_chain` ile yeniden doğrulanmaz.
- Yinelenen ya da bilinmeyen alanları olan satırlar hiçbir zaman kayıt olarak kabul edilmez. Zincirli her satır kanonik serileştirmesiyle bayt bayt aynı olmalıdır. Bir çökmenin bıraktığı parçalı son satır, bir sonraki eklemeden önce kırpılır; ancak yalnızca sonundaki satır sonu kaybolmuş tam bir kayıt ise satır sonu geri yüklenir. Parçalı bir kuyruk, bir sonraki turu tam tur olarak zorlar.
- Teslimat en az bir kez'dir. Bir ekleme dayanıklı hale geldikten sonra bir sonraki adım başarısız olursa (örneğin hem fsync hem de truncate başarısız olursa), consumer mesajı yeniden teslim eder ve defter aynı `id` ile ikinci bir kayıt tutabilir. Zincir geçerli kalır ve yinelenen kayıt `id` ile bulunur.

Durdurulmuş saklama: budama yapabilecek bir turdan önce worker tüm zinciri doğrular. Bozuk satır (aşırı iç içe ya da aralık dışı bir sayı içerenler dahil), yinelenen ya da bilinmeyen alan, kanonik olmayan satır, kopuk bağlantı ya da sonlu olmayan `received_at` budamayı durdurur: hiçbir kayıt silinmez, worker her turda `audit retention halted until the ledger is repaired` mesajını hata düzeyinde loglar ve `/health` `halted` döner. Kurtarmak için günlükteki satır numarasını bulun ve `audit.jsonl` ile `audit.jsonl.anchor` dosyalarını `JsonlFileSink(path).verify()` komutunun geçtiği bir kopyadan geri yükleyin; saklama bir sonraki turda devam eder. Budanabilecek hiçbir şey süresi dolmadığında atlanan tur zinciri yeniden doğrulamaz; bu yüzden durdurma, budamanın gerektiği ilk turda ortaya çıkar.

Saklama durumu: bir ledger'ı yalnızca bir worker süreci yazar ve durum yalnızca o süreci anlatır. Her turda worker `audit.jsonl.status` dosyasını şu alanlarla yazar: `state` (`disabled`, `ok`, `halted`, `failed` veya `blocked`), `last_success_at`, `consecutive_failures`, `checked_at` ve `interval_seconds` (etkin dönem). `blocked` yalnızca bir yaş sınırı ayarlıyken yazılır. Saklama devre dışıyken, aynı süreçteki bir heartbeat görevi her dönemde `disabled` yazar. Durum hiçbir kayıt içeriği taşımaz. `ok`, son saklama turunun başarılı olduğu anlamına gelir; defterin doğrulandığı anlamına gelmez. İlk tur bitene kadar henüz bir durum dosyası yoktur; bu yüzden `/health` ilk penceresi içinde `not_reported` döner. `GET /health` denetim API'si üzerinden `retention` alanını bildirir:

- `ok` ve `disabled`: 200.
- `halted`, `failed` ve `blocked`: 503 ile `status: degraded`.
- `stale`: `checked_at`, `interval_seconds` değerinin iki katı artı beş dakikadan daha eskidir. 503. Durmuş bir worker ya da görevi ölmüş bir consumer ya da retention yazmayı bırakır ve bu durum izler.
- `missing`: durum dosyası yok ve API bu pencereden uzun süredir ayakta. 503. Her worker bir durum yazar; bu yüzden eksik bir dosya, çalışan bir worker olmadığı ya da yazamadığı anlamına gelir.
- `unreadable`: dosya var ama okunamıyor ya da bozuk. 503.
- Tüketici durumu, kalp atışı dosyasından: `consumer_failing`, üç veya daha fazla ardışık hata ya da beş dakikadan eski bir hata serisi anlamına gelir. 503. `consumer_unreadable`, dosyanın bozuk olduğu anlamına gelir. 503. Eksik bir tüketici dosyası sağlıklıdır.
- `not_reported`: API başladıktan sonraki ilk pencerede henüz durum dosyası yok. 200.

Image HEALTHCHECK, 2xx dışındaki her yanıtı sağlıksız sayar; bu yüzden yalnızca durum kodu bu durumları görünür kılar. Denetim API'si defter birimini salt okunur bağlar: yalnızca defteri ve yan dosyalarını okur. Bir `failed` durumu, bir sonraki başarılı turda temizlenir; bu, tam bir dönem sonra olabilir.

### Gateway yönlendirmesi ve hata durumunda kapalı davranış

Gateway yönlendirmesi açıkken bulut LLM çağrıları gateway kuyruğu üzerinden gider; API bunları asla doğrudan yapmaz. Yönlendirme, saklanan `use_gateway` ayarı açıksa ya da `USE_GATEWAY_DEFAULT=true` onu zorluyorsa açıktır. Ortam değişkeni saklanan kapalı değeri geçersiz kılar: her çağrıda okunur ve veritabanına hiçbir şey yazılmaz. `USE_GATEWAY_DEFAULT` değerini false yapmak ya da hiç ayarlamamak, saklanan değerin eskisi gibi belirleyici olmasını sağlar. API şu kuralları uygular:

- `USE_GATEWAY_DEFAULT=true` iken ne `SEPTUM_QUEUE_URL` ne de `SEPTUM_QUEUE_DIR` ayarlıysa: API başlamayı reddeder.
- Yönlendirme açık ama kurulu bir gateway istemcisi yoksa (örneğin `USE_GATEWAY_DEFAULT=true` ya da kuyruk yapılandırılmadan kaydedilmiş açık bir `use_gateway`): çağrı `gateway_not_configured` koduyla başarısız olur. Doğrudan bulut çağrısı, Ollama fallback'i yoktur ve devre kesici hiç değiştirilmez. Sohbet, kodlu hatayı akışında döndürür; LLM ayarları test çağrısı aynı kodla HTTP 400 yanıtı verir.
- Gerçek bir gateway hatası (zaman aşımı, upstream hatası ya da gateway'de eksik sağlayıcı anahtarı) doğrudan bulut hatası gibi ele alınır: devre kesiciye yazılır ve çağrı Ollama'ya düşer.
- `llm_provider=ollama` her zaman yerel çalışır; `use_gateway` ya da `USE_GATEWAY_DEFAULT` değeri ne olursa olsun.

Boolean `*_DEFAULT` bayrakları, örneğin `USE_GATEWAY_DEFAULT`, yalnızca `1`, `true`, `yes`, `on` ve `0`, `false`, `no`, `off` değerlerini kabul eder (büyük/küçük harf fark etmez). Boş değer varsayılanı kullanır. Tüm bu bayraklar api başlarken denetlenir. Başka her değer reddedilir; hata, değişkenin adını söyler ama değerini göstermez. Tam sayı `*_DEFAULT` bayrakları, her bayrağın alt sınırı ile 2147483647 arasında tam sayı olmalıdır; `RAG_RELEVANCE_THRESHOLD_DEFAULT` ise 0.0 ile 1.0 arasında sonlu bir sayı olmalıdır. Boolean ya da sayısal `*_DEFAULT` bayraklarından herhangi birinde (`USE_GATEWAY_DEFAULT` dahil) geçersiz bir değer, api başlatmayı durdurur.

Saklanan `use_gateway` değeri kurulum tamamlandığında `USE_GATEWAY_DEFAULT` değerinden kopyalanır. Panel bu değeri göstermez.

---

## LLM sağlayıcıları

Septum, aşağıdaki sağlayıcıların herhangi bir kombinasyonunu kullanabilir:

| Sağlayıcı | Anahtarı nereden alırsınız | Ne zaman kullanılır |
|---|---|---|
| **Ollama** (yerel) | Tam yığında paketli; standalone için [ollama.com](https://ollama.com) | Semantik PII katmanı, Otomatik RAG sınıflandırıcısı, ücretsiz yerel sohbet |
| **Anthropic** | [console.anthropic.com](https://console.anthropic.com) | Claude Opus / Sonnet / Haiku — en kaliteli yanıtlar |
| **OpenAI** | [platform.openai.com](https://platform.openai.com) | GPT-4, GPT-4 Turbo, GPT-4o |
| **OpenRouter** | [openrouter.ai](https://openrouter.ai) | Tek anahtarla 100'den fazla modele birleşik API |

**Verinin nereye gittiği.** Bulut sağlayıcısı yalnızca maskelenmiş mesajları alır. Maskeleme bir tanımlayıcıyı kaçırabilir; bu nedenle metnin kalıntı kişisel veri içerebileceğini varsayın. Bir bulut çağrısı başarısız olursa yerel Ollama modeli aynı maskelenmiş mesajları alır. Sağlayıcı, maskelenmiş istem bakımından genellikle veri işleyen veya alıcı konumundadır; bu bir sözleşme meselesidir. Aktarımın yurt dışına aktarım sayılıp sayılmadığı, sağlayıcının istekleri işlediği yer ile sizin hukuk alanınıza bağlıdır. Aktarımın izinli olup olmadığına ve hangi güvencelerin gerektiğine karar vermek operatörün sorumluluğundadır. Ollama yedeği yapılandırılmış Ollama adresini kullanır; bu adres kendi host'unuzda veya ağınızda değilse o da bir alıcıdır.

**Önerilen Ollama modelleri:**

- `llama3.2:3b` — 2 GB, hızlı; Otomatik RAG sınıflandırması için yeterli
- `aya-expanse:8b` — 4.7 GB, semantik PII tespiti için **önerilen**; benchmark varsayılanı
- `qwen2.5:14b` — 8.4 GB; RAM'iniz yetiyorsa daha yüksek kalite

İstediğiniz zaman ek model indirebilirsiniz:

```bash
docker compose exec ollama ollama pull aya-expanse:8b
docker compose exec ollama ollama list     # indirilenleri listeler
```

Sohbet için ve semantik tespit için kullanılacak modeller, sihirbazdan sonra Ayarlar → LLM sekmesinden ayrı ayrı seçilebilir.

---

## Veri kalıcılığı ve volume'lar

Compose dosyaları, container yeniden başlatmaları ve yeniden derlemeler arasında veri kaybı yaşanmaması için isimlendirilmiş Docker volume'ları tanımlar. Standalone dosyası aşağıdaki sabit adları kullanır; tam yığın (`docker-compose.yml`) ve air-gapped dosyası adların başına compose proje adını ekler (örneğin `septum_septum-data`).

| Volume | Container içindeki yol | İçerik |
|---|---|---|
| `septum-data` | `/app/data` | SQLite veritabanı (kullanılıyorsa), `config.json`, şifreleme anahtarları |
| `septum-uploads` | `/app/uploads` | Yüklenen orijinal dosyalar, AES-256-GCM ile şifreli |
| `septum-anon-maps` | `/app/anon_maps` | Doküman başına PII placeholder eşlemeleri, şifreli |
| `septum-vector-indexes` | `/app/vector_indexes` | FAISS embedding'leri |
| `septum-bm25-indexes` | `/app/bm25_indexes` | BM25 kelime indeksleri |
| `septum-models` | `/app/models` | Önbelleklenmiş HuggingFace, Whisper ve PaddleOCR modelleri |
| `septum-pgdata` | `/var/lib/postgresql/data` | PostgreSQL veri dizini (yalnızca compose) |
| `septum-redis-data` | `/data` | Air-gapped yığının yerel Redis'i (hız sınırları, önbellek) |
| `ollama-data` | `/root/.ollama` | İndirilmiş Ollama modelleri (tam yığın ve air-gapped dosyası) |
| `septum-audit-log` | `/var/log/septum` | Denetim log dosyaları (tam yığın ve gateway dosyası) |
| `septum-mcp-scan` | `/data/scan` | MCP tarama dizini, salt okunur bağlanır (tam yığın ve air-gapped dosyası) |

**Yedekleme.** Tutarlı bir yedek iki parçadan oluşur: bir PostgreSQL dump ve şifreli volume'lar (uploads + anon-maps + vector/BM25 indeksleri). Şifreleme anahtarı `septum-data/config.json` dosyasındadır — bu anahtarı kaybederseniz şifreli tüm volume'lar okunamaz hale gelir. Veritabanı dökümü çıkarılmış doküman metnini içerir; yedekleri de veri kadar koruyun.

```bash
# Postgres dump'ı al (yalnızca tam yığın)
docker compose exec -T postgres pg_dump -U septum septum > septum-$(date +%F).sql

# İsimlendirilmiş volume'ları tar'la (standalone ve tam yığında çalışır)
for v in data uploads anon-maps vector-indexes bm25-indexes models; do
  name=$(docker volume ls -q --filter "label=com.docker.compose.volume=septum-$v" | head -n 1)
  name=${name:-septum-$v}
  docker volume inspect "$name" >/dev/null 2>&1 || { echo "volume $name not found, skipped" >&2; continue; }
  docker run --rm -v "$name":/src -v "$PWD":/dst alpine \
    tar czf /dst/septum-$v-$(date +%F).tar.gz -C /src .
done
```

Döngü, gerçek volume adını compose'un volume'a koyduğu etiketten bulur; bu yüzden proje öneki fark etmez. Host'ta yalnızca tek bir Septum yığını çalışırken kullanın.

**Geri yükleme** aynı akışın tersidir: yığını durdurun, mevcut volume'ları `docker volume rm` ile silin, arşivleri yeniden açıp yığını başlatın.

---

## Güncelleme

Septum semver'a uyar. Minor sürümler yapılandırmayı bozmaz; major sürümler [Changelog](../changelog.md) içinde açık göç notlarıyla duyurulur.

```bash
git pull                                    # compose dosyası ara ara değişir
docker compose up -d --build                # image'ları yeniden derler, container'ları yeniden yaratır
docker compose logs -f api                  # migration'ı izleyin
```

Alembic migration'ları api boot anında otomatik uygulanır. Bir migration başarısız olursa api container'ı açıklayıcı bir hata mesajıyla sonlanır; log'u inceleyip sebebi çözdükten sonra `up -d --build`'i tekrarlayın.

Standalone varyantı için aynı komutu kendi compose dosyasıyla çalıştırın:

```bash
git pull
docker compose -f docker-compose.standalone.yml up -d --build
```

Yeniden derleme boyunca veriler korunur çünkü volume'lar isimlendirilmiş volume'lardır.

**Bu sürümdeki kırıcı değişiklikler.**

- Kurulum artık `localhost` dahil her adresten kurulum anahtarı gerektirir. Loopback istisnası kalktı; anahtarsız kurulum istekleri `403` alır.
- API ve diğer servis portları varsayılan olarak `127.0.0.1` adresine bağlanır (bkz. [Yayımlanan portlar](#yayımlanan-portlar)). API'ye panel üzerinden, 3000 numaralı porttan erişin.
- Compose sabit bir alt ağ kullanır (varsayılan `172.28.70.0/24`; `SEPTUM_SUBNET`, `SEPTUM_DYNAMIC_IP_RANGE` ve `SEPTUM_WEB_IP` bunu değiştirir). Compose ağın değiştiğini bildirirse, başlatırken kullandığınız aynı `-f` dosyasıyla `docker compose -f <dosya> down` komutunu bir kez çalıştırın (örneğin `docker compose -f docker-compose.airgap.yml down`); volume'lar korunur. Ardından `docker compose -f <dosya> up -d --build` ile yeniden başlatın. Özel bir `SEPTUM_SUBNET` kullanıyorsanız `SEPTUM_WEB_IP` bu alt ağın içinde, ama `SEPTUM_DYNAMIC_IP_RANGE` dışında olmalıdır.
- `config.json` içindeki `rate_limit` değeri `60/minute` (önceki sürümlerin yazdığı değer) ise yeni varsayılan olan `600/minute` olarak okunur.
- `docker-compose.gateway.yml`, `SEPTUM_GATEWAY_REDIS_BIND` ayarlanmadan başlamaz (Redis önceden tüm arayüzlerde yayımlanıyordu).
- Varsayılan rate limit artık her API route'unda uygulanır; önceki sürümler bunu hiç uygulamıyordu.
- **Gateway kuyruğu, bölge ayrımı.** Air-gapped dosyası artık api (8000), PostgreSQL ve Redis portlarını yayımlamaz. Bunun yerine `docker compose -f docker-compose.airgap.yml exec <servis> ...` kullanın.
- **Gateway kuyruğu, air-gapped host.** Air-gapped dosyası `.env` içinde `SEPTUM_GATEWAY_QUEUE_ADDR` ve `SEPTUM_QUEUE_API_PASSWORD` değerlerini ister; bunlar olmadan compose başlamaz. Bu bölgeden yapılan bulut çağrıları artık gateway kuyruğu üzerinden gider.
- **Air-gapped host'ta Ollama.** api, Ollama'ya `http://ollama:11434` adıyla erişir; bu, bölge içindeki bir container'dır. Host Ollama relay'i kaldırılmıştır. Ayarlarda kayıtlı `host.docker.internal` Ollama URL'i orada artık çalışmaz; Ayarlar'dan değiştirin. Modeller `ollama-pull` ile yüklenir (bkz. [3. varyant](#3-yalnız-air-gapped-bölge)).
- **Model ön yüklemesi.** Air-gapped host'un modelleri, bölge dışında, bağlantılı bir makinede `docker run` ile indirilir. Komutlar için [3. varyanta](#3-yalnız-air-gapped-bölge) bakın. Bu iş için `docker compose run` artık çalışmaz; internal ağa katıldığı için.
- **Gateway host'u.** Gateway Redis'inin `default` kullanıcısı kapalıdır ve bu host'ta `REDIS_PASSWORD` kullanılmaz. Eski `redis://:<parola>@...` biçimindeki kuyruk URL'leri çalışmaz; compose dosyasının dört `SEPTUM_QUEUE_*_PASSWORD` değerinden oluşturduğu servise özgü URL'leri kullanın.
- **Tam yığın.** `docker-compose.yml` için `.env` içinde dört `SEPTUM_QUEUE_*_PASSWORD` değeri gerekir; bunlar olmadan compose başlamaz.
- **Mevcut air-gapped kurulumlar.** Dosyada `USE_GATEWAY_DEFAULT=true` iken gateway yönlendirmesi, saklanan `use_gateway` ayarı ne derse desin zorlanır. Saklanan `use_gateway` değeri false olan mevcut bir air-gapped kurulum artık gateway üzerinden zorlanır: bulut çağrıları doğrudan gitmeyi bırakır ve kuyruktan geçer. Gateway host'u çalışıp kuyruk değişkenleri ayarlanana kadar bu çağrılar `gateway_not_configured` alır. Saklanan değer değişmez; bu yüzden `USE_GATEWAY_DEFAULT` değerini false yapmak saklanan davranışı geri getirir.
- **Zaman aşımları.** `SEPTUM_GATEWAY_TIMEOUT_SECONDS` değerini artırdığınızda `SEPTUM_GATEWAY_CLIENT_TIMEOUT_SECONDS` değerini de buna uygun artırmanız gerekir. `SEPTUM_GATEWAY_TIMEOUT_SECONDS` artık deneme başına toplam süre sınırıdır. Önceki sürümler httpx faz-bazlı zaman aşımlarını kullanıyordu; bunlar yavaş bir yanıtı sınırlamıyordu.
- **Başlatma reddi.** `USE_GATEWAY_DEFAULT=true` iken, `SEPTUM_QUEUE_URL` ya da `SEPTUM_QUEUE_DIR` ayarlı değilse api başlamayı reddeder.
- **Kuyruk Redis veri volume'u.** Kuyruk Redis'inin artık veri volume'u yoktur. Mevcut kurulumlar, başlamadan önce eski kuyruk Redis veri volume'unu kaldırmalıdır; içinde hâlâ eski kuyruk verisi bulunur. Gateway host'unda `<project>_septum-gateway-redis-data` her zaman kaldırılabilir. Tam geliştirme yığını çalışan bir host'ta `<project>_septum-redis-data` yalnızca aynı proje dizininden air-gapped yığını da çalıştırmayan bir host'ta kaldırılmalıdır; çünkü iki yığın bu volume adını kullanır. (`<project>` Compose proje adıdır.)

**Yükseltme sonrası model önbellekleri.** Önceki sürümden kalan model önbellekleri sabitlenmiş commit'lerle eşleşmeyebilir. Bir sonraki çevrimiçi açılışta, bekleyen denetlenmemiş dosyalar içeren sabitlenmiş bir snapshot temizlenir ve bir kez yeniden indirilir; çevrimdışıysa, yeniden çekilene kadar reddedilir. Air-gapped bir kurulum, `fetch` komutunu bağlantılı bir host'ta yeniden çalıştırıp models volume'unu ve user-pins dosyasını kopyalamalıdır; ayrıntılar [3. varyant](#3-yalnız-air-gapped-bölge) bölümündedir. Kayıtlı commit'siz özel NER kimliği `ner_model_unpinned` koduyla, openai-whisper kayıt defteri dışındaki bir Whisper model adı (yerel bir checkpoint yolu gibi) ise `whisper_model_not_allowed` koduyla reddedilir; ikisi de Ayarlar'dan değiştirilmelidir. Ön yükleme kapalı değilse, başlangıç günlüğü reddedilecek her saklı özel NER modeli veya Whisper modeli için yalnızca hata kodlarıyla uyarı verir.

**Yayınlanmış `byerlikaya/septum` image'ından geliyorsanız.** Septum artık container image'ı yayınlamıyor; Docker Hub'daki eski image'lar duruyor ancak güncellenmiyor. Yalnızca eski container'ı kaldırın, ardından standalone compose dosyasını derleyip başlatın. Container'ı kaldırmak volume'ları silmez ve compose dosyası aynı volume adlarını kullanır (`septum-data`, `septum-uploads`, `septum-anon-maps`, `septum-vector-indexes`, `septum-bm25-indexes`, `septum-models`); böylece verileriniz, `config.json` ve şifreleme anahtarınız aynen kullanılır.

```bash
docker rm -f septum
git clone https://github.com/byerlikaya/Septum.git && cd Septum
docker compose -f docker-compose.standalone.yml up -d --build
```

Standalone compose dosyasını sabit volume adlarından önce çalıştırdıysanız verileriniz önekli volume'larda durur (örneğin `septum_septum-data`). Her birini bir kez sabit adlı volume'a kopyalayın; yukarıdaki tablodaki her volume için tekrarlayın:

```bash
docker run --rm -v septum_septum-data:/from -v septum-data:/to alpine cp -a /from/. /to/
```

Taşıma sırasında asla `docker volume prune` çalıştırmayın: hiçbir container'ın kullanmadığı tüm volume'ları, verilerinizi tutanlar dahil, siler.

---

## Sorun giderme

**Sihirbaz "Redis / PostgreSQL bağlantısı sınanıyor" adımında asılı kalıyor.** Compose dosyası `POSTGRES_PASSWORD` ve `REDIS_PASSWORD` değerlerini zorunlu tutar; `.env` boşsa veri container'ları hiç başlamamıştır. `docker compose logs postgres redis` ile bakın ve değişkenleri doldurup yeniden başlatın.

**Ollama adımı "connection refused" hatası veriyor.** Tam yığın: Ollama container'ı ilk modeli indiriyor olabilir; 30-60 saniye bekleyip tekrar deneyin. Standalone: Ollama paketli değildir — ya host'a kurun (`brew install ollama`) ve sihirbaza `http://host.docker.internal:11434` adresini verin, ya da bulut sağlayıcıya geçin.

**Sihirbaz "Kurulum anahtarı geçersiz veya süresi dolmuş" diyor.** Anahtarı yazdırıldığı gibi tam olarak girin. Anahtar, kurulum tamamlanana kadar yeniden başlatmalarda aynı kalır. `SEPTUM_SETUP_TOKEN` ayarladıysanız bu değeri girin; değişken ayarlıyken günlük satırı `Septum setup is open; use the token set in SEPTUM_SETUP_TOKEN.` yazar. Aksi hâlde en son satırı şöyle okuyun: `docker compose logs api | grep 'setup token'` (standalone: `docker compose -f docker-compose.standalone.yml logs septum | grep 'setup token'`). Günlükte anahtar satırı yoksa, veritabanında zaten bir kullanıcı olduğu için kurulum kapalıdır. Bir dakikada 10 yanlış denemeden sonra sihirbaz çok fazla deneme bildirir; bir dakika bekleyip tekrar deneyin.

**api başlangıçta "USE_GATEWAY_DEFAULT is true but neither SEPTUM_QUEUE_URL nor SEPTUM_QUEUE_DIR is set" hatasıyla çıkıyor.** Bu, fail-closed başlatma kontrolüdür. Kuyruk değişkenlerini ayarlayın ([Gateway kuyruğu değişkenleri](#gateway-kuyruğu-değişkenleri)) ya da `USE_GATEWAY_DEFAULT` değerini kaldırın. Air-gapped dosyası kuyruk olmadan çalışamaz.

**Sohbette ya da LLM ayarları testinde `gateway_not_configured` görünüyor.** Kayıtlı bir ayar LLM çağrılarını gateway üzerinden yönlendiriyor, ama bu api'nin bir kuyruk istemcisi yok. Septum burada doğrudan bulut çağrısına ya da Ollama'ya düşmez. Kuyruğu yapılandırın ve gateway host'unun çalıştığından emin olun. Panel `use_gateway` değerini göstermez; saklanan değer, kurulum tamamlandığında `USE_GATEWAY_DEFAULT` değerinden kopyalanır.

**Port 3000 kullanımda.** Makinenizde başka bir şey bu portu tutuyor. Ya o süreci durdurun ya da Septum'un bağlanma portunu değiştirin: bir compose override'ında `-p 3001:3000` kullanın veya `docker-compose.yml` içindeki `ports:` satırını düzenleyin.

**Paralel yükleme sırasında `disk I/O error`.** SQLite'ın WAL'ı tek yazıcı kilidine sahiptir; havuz 50 bağlantıya kadar sığar ama yoğun paralel OCR + Whisper yükü yine de doyurabilir. PostgreSQL'e geçin (compose varyantları bunu zaten yapar) ya da Ayarlar → İngest sekmesinden eşzamanlılık üst sınırını düşürün.

**İlk ses yüklemesinde Whisper modeli indirme zaman aşımına uğruyor.** Model tembel indirilir ve büyük boyutlarda dakikalar sürebilir. İlk gerçek yüklemeden önce modeli Ayarlar → İngest sekmesinden önceden ısıtın veya `docker compose exec api python -c "import whisper; whisper.load_model('base')"` çalıştırın.

**Docker Desktop "yetersiz kaynak" diyor.** Docker Desktop → Ayarlar → Kaynaklar altından VM belleğini en az 8 GB'a çıkarın. Ollama + Presidio + Whisper birlikte RAM'i iştahla tüketir.

**Başka bir şey.** İlgili log akışına bakın:

```bash
docker compose logs -f api       # backend, pipeline, auth, audit
docker compose logs -f web       # Next.js build + runtime
docker compose logs -f ollama    # model indirme, GPU tespiti
docker compose logs -f postgres  # veritabanı init / migration
```

Sebep hâlâ ortaya çıkmıyorsa hata loguyla, kullandığınız compose varyantıyla ve OS + Docker sürüm bilgisiyle birlikte [GitHub issue](https://github.com/byerlikaya/Septum/issues) açın.

---

## Kaldırma

Temiz kaldırma:

```bash
docker compose down -v           # container'ları durdurur, isimlendirilmiş volume'ları siler
```

Kritik bayrak `-v`'dir; bu olmadan volume'lar kalır ve sonraki `docker compose up` eski verilerinizi diriltir. "Her ihtimale karşı" tutmak istiyorsanız `-v` olmadan çalıştırın ve önce volume'ları yedekleyin ([Veri kalıcılığı](#veri-kalıcılığı-ve-volumelar) bölümüne bakın).

Standalone varyantı için:

```bash
docker compose -f docker-compose.standalone.yml down -v
```

Kaynaktan kurulum için klonladığınız dizini ve Septum'un (varsa) `~/.cache/septum` altına yazdığı Python / npm önbelleklerini silmek yeterlidir.

---

<p align="center">
  <a href="./index.md"><strong>🏠 Ana Sayfa</strong></a>
  &nbsp;·&nbsp;
  <strong>🚀 Kurulum</strong>
  &nbsp;·&nbsp;
  <a href="benchmark.md"><strong>📈 Benchmark</strong></a>
  &nbsp;·&nbsp;
  <a href="features.md"><strong>✨ Özellikler</strong></a>
  &nbsp;·&nbsp;
  <a href="architecture.md"><strong>🏗️ Mimari</strong></a>
  &nbsp;·&nbsp;
  <a href="document-ingestion.md"><strong>📊 Doküman İşleme</strong></a>
  &nbsp;·&nbsp;
  <a href="screenshots.md"><strong>📸 Ekran Görüntüleri</strong></a>
</p>
