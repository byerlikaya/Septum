# septum-mcp

> 🇬🇧 [English version](README.md)

[Septum](https://github.com/byerlikaya/Septum)'un gizlilik öncelikli PII maskeleme hattını **MCP uyumlu her istemciye** açan Model Context Protocol sunucusu. MCP, açık ve sağlayıcıdan bağımsız bir [spesifikasyon](https://modelcontextprotocol.io)dur; sunucu resmi Python SDK'si üzerine yazılmıştır ve üç standart taşıma katmanının tamamını destekler:

- **stdio** (varsayılan) — sunucuyu alt süreç olarak başlatan yerel istemciler için: Claude Code, Claude Desktop, Cursor, Windsurf, Zed, Cline, Continue ile LangChain / LlamaIndex MCP adaptörleri.
- **streamable-http** — uzak, container içi ya da tarayıcıya gömülü istemciler için modern HTTP taşıması. Statik bir bearer token ile güvenceye alınır.
- **sse** — eski HTTP + Server-Sent Events taşıması; henüz streamable-http'ye geçmemiş istemciler için tutulur.

Tespitin tamamı `septum-core` üzerinden **yerelde** yürür. Ham PII makineden dışarı çıkmaz. Sunucu yalnızca taşıma görevi görür: çekirdek motoru sarıp çağıranın eline beş araç (ve isteğe bağlı bir debug aracı olan `get_session_map`'i) bırakır.

## Araçlar

| Araç | Açıklama |
|---|---|
| `mask_text` | Metindeki PII'yi tespit eder ve maskeler. Maskelenmiş metin + `session_id` döner. |
| `unmask_response` | LLM cevabındaki placeholder'ları `session_id` üzerinden gerçek değerlere geri yazar. Yalnızca o session'ın ürettiği placeholder'lar, session başına en fazla `SEPTUM_MCP_MAX_UNMASK_PLACEHOLDERS` farklı placeholder geri yazılır. |
| `detect_pii` | Session tutmadan metindeki PII'yi tarar. |
| `scan_file` | Yerel bir dosyayı (`.txt`, `.md`, `.csv`, `.json`, `.pdf`, `.docx`) okur ve içeriğini tarar. HTTP/SSE üzerinde `SEPTUM_MCP_FILE_ROOT` gerektirir. |
| `list_regulations` | Yerleşik regülasyon paketlerini ve tanımladıkları varlık tiplerini listeler. |
| `get_session_map` | **İsteğe bağlı, yalnız stdio, varsayılan olarak kapalı.** Bir session için `{orijinal → placeholder}` haritasını döner (yalnızca debug amaçlı). Bkz. `SEPTUM_MCP_EXPOSE_SESSION_MAP`. |

## Kurulum

```bash
pip install septum-mcp
```

Paket henüz `refactor/modular-architecture` dalında geliştirilirken kaynaktan:

```bash
pip install -e packages/core
pip install -e packages/mcp
```

## İstemci yapılandırması

Her MCP istemcisinin bir sunucu kaydetme yolu vardır — aşağıdaki blok Claude Code'un (`~/.claude/mcp.json`), Claude Desktop'un (macOS'ta `~/Library/Application Support/Claude/claude_desktop_config.json`), Cursor'un (Ayarlar → MCP) ve diğer pek çok istemcinin kullandığı kanonik `mcpServers` JSON biçimidir. Farklı bir yapılandırma dosyası tutan editörler (Zed, Cline, Continue, …) aynı alanlar için kendi arayüzlerini sunar: bu dokümandan ihtiyaç duyacağınız tek şey `command`, `args` ve `env` alanlarıdır.

### Yöntem A — pip ile kurulu (önerilen)

```json
{
  "mcpServers": {
    "septum": {
      "command": "septum-mcp",
      "env": {
        "SEPTUM_REGULATIONS": "gdpr,kvkk",
        "SEPTUM_LANGUAGE": "tr",
        "SEPTUM_USE_NER": "true"
      }
    }
  }
}
```

### Yöntem B — uvx (izole ortam, `pip install` gerekmez)

```json
{
  "mcpServers": {
    "septum": {
      "command": "uvx",
      "args": ["septum-mcp"],
      "env": {
        "SEPTUM_REGULATIONS": "gdpr",
        "SEPTUM_USE_NER": "false"
      }
    }
  }
}
```

### Yöntem C — repo üzerinde yerel geliştirme

```json
{
  "mcpServers": {
    "septum": {
      "command": "python",
      "args": ["-m", "septum_mcp.server"],
      "env": {
        "SEPTUM_REGULATIONS": "gdpr",
        "PYTHONPATH": "/Septum/dizininin/mutlak/yolu/packages/core:/Septum/dizininin/mutlak/yolu/packages/mcp"
      }
    }
  }
}
```

## Uzak HTTP dağıtımı

MCP istemcisi bir alt süreç başlatamadığında HTTP taşımasına geçin: web tabanlı ajanlar, tarayıcı eklentileri, paylaşılan takım sunucusu ya da container orkestrasyon dağıtımı. Sunucu aynı beş aracı sunmaya devam eder; yalnızca taşıma değişir. `get_session_map` HTTP ya da SSE üzerinden hiçbir zaman sunulmaz.

### Sunucuyu çalıştırmak

```bash
# Token'ı bir kere üretin (bir secret manager'da saklayın):
openssl rand -hex 32

# Sunucuyu kimlik doğrulamalı başlatın
SEPTUM_MCP_HTTP_TOKEN=<rastgele-token> septum-mcp \
  --transport streamable-http \
  --host 0.0.0.0 \
  --port 8765
```

CLI bayrakları (`--transport`, `--host`, `--port`, `--token`, `--mount-path`) ilgili `SEPTUM_MCP_*` ortam değişkenlerini geçersiz kılar.

Token olmadığında sunucu kimlik doğrulamasını uygulamaz — bu durum açıkça (başlangıçta log'a) duyurulur ve yalnızca localhost için tasarlanmıştır.

### İstemci yapılandırması (streamable-http)

```jsonc
{
  "mcpServers": {
    "septum": {
      "url": "https://mcp.example.com/mcp",
      "headers": {
        "Authorization": "Bearer <token>"
      }
    }
  }
}
```

URL yolu varsayılan olarak streamable-http için `/mcp`, sse için `/sse`'dir — `--mount-path` ya da `SEPTUM_MCP_HTTP_MOUNT_PATH` ile değiştirilebilir.

### Docker

Septum container image'ı yayınlamaz; image'ı depoyu klonlayarak kendiniz derleyin:

```bash
docker build -f docker/mcp.Dockerfile -t septum-mcp:local .
docker run -p 8765:8765 \
  -e SEPTUM_MCP_HTTP_TOKEN=<rastgele-token> \
  -e SEPTUM_MCP_HTTP_HOST=0.0.0.0 \
  septum-mcp:local
```

Ya da docker-compose ile `docker-compose.yml` içindeki `mcp` profile'ını açın:

```bash
SEPTUM_MCP_HTTP_TOKEN=<rastgele-token> \
  docker compose --profile mcp up --build mcp
```

### Dağıtım notları

- **Üretimde mutlaka TLS arkasında çalıştırın.** Bearer token, `Authorization` header'ında olduğu gibi aktarılır; otomatik Let's Encrypt sertifikalı bir reverse proxy (Caddy, nginx, Traefik) bu iş için alışılmış yoldur.
- **`/health` uç noktası** her koşulda `200 OK` döner (kimlik doğrulamadan muaftır); böylece reverse-proxy probe'ları ve Docker `HEALTHCHECK` direktifleri token olmadan çalışır.
- **Bugün yalnızca tek kiracılıdır.** Tüm HTTP istemcileri tek bir `SeptumEngine`'i ve dolayısıyla tek bir anonimleştirme session registry'sini paylaşır. Çok kiracılı izolasyon (istemci başına ayrışmış session'lar) yol haritasındadır; şimdilik izolasyon önemliyse her kiracı için ayrı bir örnek çalıştırın.

## Ortam değişkenleri

**Çekirdek / tüm taşımalarda ortak:**

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `SEPTUM_REGULATIONS` | 17 paketin tamamı | Virgülle ayrılmış regülasyon paket id'leri (örneğin `gdpr,kvkk,hipaa`). |
| `SEPTUM_LANGUAGE` | `en` | Araç çağrısı dil belirtmediğinde kullanılacak ISO 639-1 varsayılanı. |
| `SEPTUM_USE_NER` | `true` | Transformer NER katmanını etkinleştirir. Model indirmesinden kaçınmak için `false` yapın. |
| `SEPTUM_USE_PRESIDIO` | `true` | Presidio tanıyıcı katmanını etkinleştirir. |
| `SEPTUM_SESSION_TTL` | `3600` | Boşta kalan bir anonimleştirme session'ının atılacağı saniye sayısı. `0` eviction'ı tamamen kapatır. |
| `SEPTUM_MCP_EXPOSE_SESSION_MAP` | `false` | `get_session_map` aracını kaydetmek için `true` yapın. Yalnız stdio'da geçerlidir. **MCP istemcisine ham PII döner** — güvenlik notlarına bakın. |
| `SEPTUM_MCP_MAX_UNMASK_PLACEHOLDERS` | `25` | Session başına geri yazma bütçesi: `unmask_response`'un bir session için tüm çağrıları boyunca geri yazabileceği en fazla farklı placeholder sayısı. Bütçeyi aşacak çağrı hata döner ve hiçbir şey geri yazılmaz; daha önce geri yazılmış bir placeholder'ı yeniden geri yazmak bütçeden düşmez. Pozitif tam sayı; geçersiz değerler varsayılana döner. |
| `SEPTUM_MCP_FILE_ROOT` | ayarsız | `scan_file`'ın sınırlandığı dizin. stdio'da isteğe bağlıdır; HTTP/SSE'de ayarlanmadıkça `scan_file` her yolu reddeder. Sunucu başlarken dizin olarak var olmalıdır (yoksa başlatma açık bir hatayla durur) ve o anda sabitlenir: sembolik bağlantı arkasındaki bir köke izin verilir ve hedefine sabitlenir; kök yolu sonradan değiştirilirse sınır taşınmaz, okumalar reddedilir. Sunucu bu dizinden yalnızca okur; salt okunur bağlayın (paketle gelen compose dosyaları `septum-mcp-scan`'i `/data/scan:ro` olarak bağlar). |
| `SEPTUM_MCP_MAX_FILE_BYTES` | `52428800` | `scan_file`'ın okuyacağı en büyük dosya boyutu (50 MB). |

**Yalnız HTTP taşımasında (stdio modunda yok sayılır):**

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `SEPTUM_MCP_TRANSPORT` | `stdio` | Bağlanılacak taşıma: `stdio`, `streamable-http` ya da `sse`. |
| `SEPTUM_MCP_HTTP_HOST` | `127.0.0.1` | Dinleme adresi. `0.0.0.0`'a yalnız TLS + kimlik doğrulama arkasında bağlayın. |
| `SEPTUM_MCP_HTTP_PORT` | `8765` | TCP portu. |
| `SEPTUM_MCP_HTTP_TOKEN` | ayarlı değil | `Authorization: Bearer <token>` için bearer token. Ayarlı değilse HTTP kimlik doğrulama olmadan çalışır (yalnız localhost). |
| `SEPTUM_MCP_HTTP_MOUNT_PATH` | SDK varsayılanı | URL yol öneki (streamable-http için `/mcp`, sse için `/sse`). |
| `SEPTUM_MCP_HTTP_ALLOWED_HOSTS` | ayarlı değil | Virgülle ayrılmış `Host` başlığı izin listesi (`host`, `host:port` ya da `host:*`; IPv6 köşeli parantez içinde); `--allowed-hosts` ile de verilebilir. Ayarlıysa DNS-rebinding koruması açılır: listede olmayan bir `Host` 421 alır, tarayıcının gönderdiği `Origin` da `http://` ya da `https://` artı listedeki bir girdi olmalıdır (yoksa 403). Ayarlı değilse loopback bağlanma SDK'nın yalnız-localhost kontrolünü korur, diğer bağlanmalar her `Host`'u kabul eder. Hatalı bir girdi sunucunun başlamasını durdurur. |

Desteklenen regülasyon paket id'leri: `gdpr`, `kvkk`, `ccpa`, `cpra`, `hipaa`, `pipeda`, `lgpd`, `pdpa_th`, `pdpa_sg`, `appi`, `pipl`, `popia`, `dpdp`, `uk_gdpr`, `pdpl_sa`, `nzpa`, `australia_pa`.

## Kullanım örnekleri

### Uzak modele göndermeden önce maskele

```jsonc
// Araç çağrısı
{
  "name": "mask_text",
  "arguments": {
    "text": "1234 numaralı fatura hakkında Ayşe Yılmaz'a ayse@ornek.com adresinden ulaşın.",
    "language": "tr"
  }
}

// Cevap
{
  "masked_text": "1234 numaralı fatura hakkında [PERSON_1]'a [EMAIL_ADDRESS_1] adresinden ulaşın.",
  "session_id": "e8f1...",
  "entity_count": 2,
  "entity_type_counts": { "PERSON": 1, "EMAIL_ADDRESS": 1 }
}
```

### LLM cevabını geri yaz

```jsonc
{
  "name": "unmask_response",
  "arguments": {
    "text": "[PERSON_1]'a [EMAIL_ADDRESS_1] adresinden bir takip mesajı hazırladım.",
    "session_id": "e8f1..."
  }
}
```

### Yerel bir dosyayı tara

```jsonc
{
  "name": "scan_file",
  "arguments": {
    "file_path": "/Users/ayse/notlar/musteri.pdf",
    "mask": true
  }
}
```

## Desteklenen dosya formatları

`scan_file` aracı düz metin, Markdown, CSV, JSON, PDF (`pypdf` ile) ve DOCX (`python-docx` ile) formatlarını işler. OCR (taranmış PDF, görseller) ve ses transkripsiyonu bu paketin kapsamı dışında bırakılmıştır; yüzlerce megabaytlık modeller gerektirir ve yeri Septum API/ingest hattıdır.

## Kapsam ve güvenlik notları

- **septum-mcp air-gapped'tir.** Dışarıya çıkan hiçbir ağ çağrısı yoktur. Opsiyonel NER katmanı bile modelleri yerel Hugging Face cache'inden yükler. `SEPTUM_USE_NER`'ı kapatırsanız hiç model indirmesi olmaz.
- **Session'lar yalnızca bellektedir.** Anonimleştirme haritaları sunucu süreci içinde yaşar; sunucu sonlandığında ya da session başına TTL dolduğunda düşer. Diske hiçbir şey yazılmaz.
- **`get_session_map` ham PII döndüğü için varsayılan olarak kapalıdır.** MCP istemcisini genellikle bir bulut modeli yönetir; bir aracın döndürdüğü her şey o modelin bağlamına girebilir — session haritası placeholder'ların ardındaki tüm orijinal değerleri modele verir ve maskelemeyi boşa çıkarır. `SEPTUM_MCP_EXPOSE_SESSION_MAP=true` ayarlanmadıkça araç kaydedilmez (`tools/list` içinde görünmez); ayarlansa bile yalnız stdio'da geçerlidir, HTTP ve SSE bayrağı yok sayıp uyarı loglar. Yalnızca araç çıktısını uzak bir modele iletmeyen bir istemciyle yerel debug için açın ve iş bitince yeniden kapatın.
- **`unmask_response` yalnızca session'ın ürettiği placeholder'larla sınırlıdır.** Sadece o session'ın `mask_text` ya da `scan_file` (`mask=true`) çıktısında yer alan placeholder'ları geri yazar; diğer her placeholder ve araçların hiç maskelemediği bir session'daki tüm placeholder'lar olduğu gibi kalır. Her session'ın, **tüm** çağrıları boyunca sayılan `SEPTUM_MCP_MAX_UNMASK_PLACEHOLDERS` (varsayılan `25`) farklı placeholder'lık bir geri yazma bütçesi vardır: session'ı bu bütçenin üstüne çıkaracak bir çağrı, hiçbir şey geri yazılmadan reddedilir; bu yüzden hazırlanmış bir cevabı çok sayıda küçük çağrıya bölmek sınırı aşmaya yetmez. Session'ın zaten geri yazdığı bir placeholder'ı yeniden geri yazmak bütçeden düşmez. Bütçe içinde geri yazılan değerler yine ham PII'dir; geri yazılmış çıktıyı buna göre ele alın.
- **`scan_file` sınırlandırılmıştır.** Sembolik bağlantıları, dotfile'ları ve nokta ile başlayan bir dizinin altındaki her şeyi (`.env`, `.ssh/`, `.git/`) ve `SEPTUM_MCP_MAX_FILE_BYTES` üstündeki dosyaları reddeder. Tek bir dizine sınırlamak için `SEPTUM_MCP_FILE_ROOT` ayarlayın. stdio'da kök isteğe bağlıdır; HTTP ve SSE'de kök ayarlanana kadar `scan_file` her yolu reddeder, böylece uzak bir istemci sunucu sürecinin okuyabildiği keyfi dosyaları asla okuyamaz. Kök, sunucu başlarken var olmalıdır ve o anda çözümlendiği dizine sabitlenir; kök yolu sonradan değiştirilirse (örneğin bir sembolik bağlantıyla) okumalar bağlantıyı izlemek yerine reddedilir. Kök dışındaki bir yol, bulunmayan bir dosya ve bir dotfile aynı hatayı alır; hata ne yolu ne de kökü içerir.
- **HTTP modu, loopback olmayan her bağlanmada bearer token ister.** `SEPTUM_MCP_HTTP_TOKEN`, `/health` dışındaki her isteğin önünde durur; tokensiz ve loopback olmayan bir host ise başlamayı reddeder. Sunucunun önünde mutlaka TLS sonlandırın (reverse proxy) ki token düz metinle gitmesin.
- **Loopback olmayan bağlanmalarda `Host` başlığını sabitleyin.** `0.0.0.0`'da SDK `Host` / `Origin` kontrolü yapmaz; DNS-rebinding korumasını yeniden açmak için `SEPTUM_MCP_HTTP_ALLOWED_HOSTS`'u istemcilerin kullandığı adlara ayarlayın (örneğin `mcp.internal:*`). Bir reverse proxy arkasında sunucu, proxy'nin ilettiği `Host`'u görür; bu yüzden proxy'yi özgün `Host` başlığını aynen iletecek şekilde yapılandırın.
- **HTTP modu bugün tek kiracılıdır.** Bağlı tüm istemciler tek bir `SeptumEngine`'i ve tek bir session registry'sini paylaşır. İzolasyon önemliyse her kiracı için ayrı bir örnek çalıştırın.

## Test paketini çalıştırma

```bash
pip install -e 'packages/core[transformers]'
pip install -e 'packages/mcp[test]'
pytest packages/mcp/tests/
```

Paket config parse'ını, dosya okuyucularını, altı aracın tamamını ve gerçek sunucuyu alt süreç olarak başlatan bir uçtan uca stdio smoke testini kapsar.
