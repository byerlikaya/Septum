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
# .env dosyasını editörünüzde açıp POSTGRES_PASSWORD ve REDIS_PASSWORD'ü doldurun
docker compose up --build
```

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

Tek bir host üzerinde tüm modüllerin yanı sıra Ollama, PostgreSQL ve Redis. Varsayılan `docker-compose.yml` bu topolojiyi üretir.

```bash
git clone https://github.com/byerlikaya/Septum.git && cd Septum
cp .env.example .env
# .env dosyasını açın ve POSTGRES_PASSWORD + REDIS_PASSWORD değerlerini doldurun (zorunlu)

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

Yalnızca air-gapped bölgeyi (api + web + PostgreSQL + Redis) çalıştırır. Host'un internet çıkışı olmamalıdır, ancak compose dosyası bunu zorlamaz. Bu ayrıma güvenmeden önce [Gateway yönlendirmesinin durumu](#gateway-yönlendirmesinin-durumu) bölümünü okuyun.

```bash
cp .env.example .env
# POSTGRES_PASSWORD ve REDIS_PASSWORD değerlerini doldurun

docker compose -f docker-compose.airgap.yml up -d --build
```

Bu topoloji `USE_GATEWAY_DEFAULT=true` kullanır; bu ayar, her LLM çağrısının `septum-queue` (Redis Streams) üzerinden internete açık host'a yönlendirilmesi içindir. Bu sürümde bu yönlendirme bağlı değildir. Bkz. [Gateway yönlendirmesinin durumu](#gateway-yönlendirmesinin-durumu).

**İnternetsiz host için modelleri hazırlama.** Air-gapped host'un internet erişimi yoktur; bu yüzden model önbelleği bağlantılı bir makinede doldurulur ve host'a kopyalanır. Sabitlemeler (pin) kodun içinde olduğu için iki makine de aynı Septum kaynak ağacını çalıştırmalıdır. Bağlantılı makinede, aynı `.env` ile:

```bash
docker compose -f docker-compose.airgap.yml build api
docker compose -f docker-compose.airgap.yml run --rm -e HF_HUB_OFFLINE=0 api python -m septum_api.cli.models fetch --whisper base
docker run --rm -v septum_septum-models:/src -v "$PWD":/dst alpine tar czf /dst/septum-models.tar.gz -C /src .
```

`fetch` komutu, varsayılan her modeli sabitlenmiş commit'inden indirip doğrular; ardından `--whisper` ile verilen Whisper modelini indirir (kullandığınız her boyut için seçeneği tekrarlayın). `--whisper` verilmezse `WHISPER_MODEL` ayarındaki model indirilir; bu ayarın varsayılanı `base`'dir. Son komut model volume'unu arşivler; volume adı compose proje önekini taşır (`Septum` adlı bir dizindeki kaynak ağacı için `septum_`). Air-gapped compose dosyası `HF_HUB_OFFLINE=1` ayarlar; bu yüzden fetch komutları `-e HF_HUB_OFFLINE=0` ile bu ayarı geçersiz kılar. Bu, indirme yapan tek adımdır ve bağlantılı host üzerinde çalışır. `septum-models.tar.gz` dosyasını air-gapped host'a kopyalayın, ardından:

```bash
docker compose -f docker-compose.airgap.yml build api
docker compose -f docker-compose.airgap.yml create
docker run --rm -v septum_septum-models:/dst -v "$PWD":/src alpine tar xzf /src/septum-models.tar.gz -C /dst
docker compose -f docker-compose.airgap.yml up -d
docker compose -f docker-compose.airgap.yml run --rm api python -m septum_api.cli.models fetch --whisper base --offline-check
```

`--offline-check` kopyalanan dosyaları ağa çıkmadan doğrular. Her model için bir satır yazdırır; herhangi bir dosya eksik ya da sabitlemesiyle uyuşmuyorsa çıkış durumu 1 olur.

Her iki host'ta `alpine` image'ının yerelde bulunması gerekir: bağlantılı makinede `docker pull alpine` çalıştırın ve image'ı `docker save` ile `docker load` üzerinden aktarın. İçinde `tar` bulunan herhangi bir image, yukarıdaki komutlarda onun yerine kullanılabilir. Air-gapped compose dosyası `api` ve `mcp` servislerinde `HF_HUB_OFFLINE=1` ayarlar; bu container'lar model indirmez. Etkinleştirildiğinde `mcp` servisi models volume'unu salt okunur (read-only) bağlar; bu yüzden yalnızca volume'da zaten bulunan ve hash denetimini geçen modelleri yükler. `api`'nin henüz yüklemediği diller için `mcp`'ye güvenmeden önce volume'u `fetch` komutuyla (tüm varsayılan sabitlemeler) doldurun.

**Özel NER modelleri.** Özel model, bağlantılı host'ta aynı `.env` ile `--user-pin` kullanılarak çekilir. Yukarıdaki gibi, bu indirme için çevrimdışı ayarı `-e HF_HUB_OFFLINE=0` ile geçersiz kılınır. Her özel model için seçeneği tekrarlayın:

```bash
docker compose -f docker-compose.airgap.yml run --rm -e HF_HUB_OFFLINE=0 api python -m septum_api.cli.models fetch --user-pin org/name@<40 karakterlik commit SHA>
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
# REDIS_PASSWORD'ü doldurun (bu host'a özgü bir değer; air-gapped host'un parolasını kullanmayın)
# SEPTUM_GATEWAY_REDIS_BIND değerini bu host'un özel veya VPN adresine ayarlayın (zorunlu; bu olmadan compose başlamaz)
# Sağlayıcı anahtarlarını girin: ANTHROPIC_API_KEY, OPENAI_API_KEY vb.

docker compose -f docker-compose.gateway.yml up -d --build
```

Gateway image'ı güvenlik invariantı gereği `septum-core`'u içermez — yalnızca placeholder ile maskelenmiş metni görür ve maskelenmiş cevapları geri yayınlar. Audit sidecar'ı her isteği varlık tipi, sayı ve regülasyon id'leriyle loglar (ham PII asla yer almaz) ve `/api/audit/export` uç noktasını SIEM entegrasyonu için açar.

**Air-gapped + internet-facing birlikte**: iki Docker host. Kuyruk, gateway host'unun `SEPTUM_GATEWAY_REDIS_BIND` üzerinde yayımlanan Redis'idir. Her host kendi `REDIS_PASSWORD` değerini kullanır. Air-gapped Redis `127.0.0.1` üzerinde kalır ve parolası gateway host'una verilmez. Yönlendirme yayımlandığında air-gapped api kuyruğa `SEPTUM_QUEUE_URL` ile bağlanacak. Redis bağlantısı düz metindir; bu yüzden yalnızca VPN veya özel ağ üzerinden çalıştırın. Kuyruk tasarım gereği yalnızca placeholder ile maskelenmiş payload taşır, ancak kuyruk üzerinden yönlendirme henüz bağlı değildir (bkz. [Gateway yönlendirmesinin durumu](#gateway-yönlendirmesinin-durumu)).

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

**Host'ta zaten bir Ollama çalışıyorsa:** port çakışmasını önlemek için paketli olanı durdurun (`docker compose stop ollama && docker compose rm -f ollama`), ardından Ollama URL'i olarak `http://host.docker.internal:11434` yazın. docker-compose.yml api container'ına `extra_hosts` ile `host.docker.internal`'ı bağlamış durumda; host'taki Ollama'ya ek bir ayar olmadan erişilir.

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

Panelin önünde ters bir proxy varsa `SEPTUM_TRUSTED_PROXIES` değişkenini panel sürecinde (web container'ının ortamında ya da `npm start` çalıştıran host'ta) proxy'nin adresi ya da CIDR'ı olarak ayarlayın; aynı boyut sınırları geçerlidir. Panel, istemci adresini proxy'nin `X-Forwarded-For` başlığından okur. Geçersiz bir girdide başlamayı reddeder. Compose dosyaları bu değişkeni ayarlamaz; `web` servisinin ortamına ekleyin.

### Yayımlanan portlar

Yalnızca panel tüm arayüzlerde dinler. Diğer yayımlanan her port, kendi bind değişkeni ayarlanmadıkça `127.0.0.1` adresine bağlanır; gateway Redis'in varsayılanı yoktur ve `SEPTUM_GATEWAY_REDIS_BIND` gerektirir:

| Port | Servis | Varsayılan bind | Değişken |
|---|---|---|---|
| 3000 | Panel (web) | tüm arayüzler | — |
| 8000 | API | `127.0.0.1` | — (standalone'da yayımlanmaz) |
| 5432 | PostgreSQL | `127.0.0.1` | — |
| 6379 | Redis (air-gapped dosyası) | `127.0.0.1` | — |
| 11434 | Ollama | `127.0.0.1` | `SEPTUM_OLLAMA_BIND` |
| 8765 | MCP sunucusu (`mcp` profili) | `127.0.0.1` | `SEPTUM_MCP_BIND` |
| 8002 | Audit API | `127.0.0.1` | `SEPTUM_AUDIT_BIND` |
| 8001 | Gateway sağlık | `127.0.0.1` | `SEPTUM_GATEWAY_HEALTH_BIND` |
| 6379 | Gateway Redis (gateway dosyası) | zorunlu | `SEPTUM_GATEWAY_REDIS_BIND` |

Docker'ın yayımladığı portlar ufw ve firewalld'nin INPUT kurallarını atlar. Yukarıdaki bind değişkenlerini kullanın; gerektiğinde DOCKER-USER kuralları ekleyin.

### İzleme

Panel artık `/metrics` isteklerini proxy'lemez. API'yi doğrudan kazıyın: compose ağı içinden `api:8000`, host üzerinden `127.0.0.1:8000`. Standalone container 8000 portunu yayımlamaz. Standalone'da bir compose override'ında `127.0.0.1:8000:8000` ile kendiniz yayımlayın. `/metrics` oturum gerektirmez, yönlendirilebilir bir adrese açmayın. `WEB_CONCURRENCY` 1'den büyükse her kazıma tek bir worker'ın sayaçlarını okur.

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

### Gateway yönlendirmesinin durumu

Gateway yönlendirmesi yayımlanana kadar air-gapped ayrımı bir çıkış (egress) garantisi değildir. Bu sürümde API `SEPTUM_QUEUE_URL` değerini yok sayar ve `USE_GATEWAY_DEFAULT=true` olsa bile LLM çağrılarını gateway kuyruğu üzerinden göndermez. Bir bulut sağlayıcısı yapılandırılmışsa API onu air-gapped host'tan doğrudan çağırır (yalnızca maskelenmiş veriyle) ve compose dosyaları bu trafiği engellemez. Şimdilik air-gapped bölgede yerel bir Ollama sağlayıcısı kullanın ve orada bulut sağlayıcısı yapılandırmayın.

---

## LLM sağlayıcıları

Septum, aşağıdaki sağlayıcıların herhangi bir kombinasyonunu kullanabilir:

| Sağlayıcı | Anahtarı nereden alırsınız | Ne zaman kullanılır |
|---|---|---|
| **Ollama** (yerel) | Tam yığında paketli; standalone için [ollama.com](https://ollama.com) | Semantik PII katmanı, Otomatik RAG sınıflandırıcısı, ücretsiz yerel sohbet |
| **Anthropic** | [console.anthropic.com](https://console.anthropic.com) | Claude Opus / Sonnet / Haiku — en kaliteli yanıtlar |
| **OpenAI** | [platform.openai.com](https://platform.openai.com) | GPT-4, GPT-4 Turbo, GPT-4o |
| **OpenRouter** | [openrouter.ai](https://openrouter.ai) | Tek anahtarla 100'den fazla modele birleşik API |

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
| `septum-redis-data` | `/data` | Redis AOF/RDB (yalnızca compose) |
| `ollama-data` | `/root/.ollama` | İndirilmiş Ollama modelleri (yalnızca tam yığın) |
| `septum-audit-log` | `/var/log/septum` | Denetim log dosyaları (tam yığın ve gateway dosyası) |
| `septum-mcp-scan` | `/data/scan` | MCP tarama dizini, salt okunur bağlanır (tam yığın ve air-gapped dosyası) |

**Yedekleme.** Tutarlı bir yedek iki parçadan oluşur: bir PostgreSQL dump ve şifreli volume'lar (uploads + anon-maps + vector/BM25 indeksleri). Şifreleme anahtarı `septum-data/config.json` dosyasındadır — bu anahtarı kaybederseniz şifreli tüm volume'lar okunamaz hale gelir.

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
