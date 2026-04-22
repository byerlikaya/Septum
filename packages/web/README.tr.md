# septum-web

> 🇬🇧 [English version](README.md)

[Septum](https://github.com/byerlikaya/Septum) için air-gapped Next.js paneli. Doküman yönetimi arayüzünü, maskelenmiş prompt'ların onay kapısını, sohbet görünümünü, regülasyon ayarlarını ve kurulum sihirbazını taşır — gizlilik öncelikli ara katmanın kullanıcıya bakan yüzüdür.

PII tespiti ve maskeleme panelin arkasında, `septum-api` + `septum-core` ikilisinde yürür. Bu paket tamamen taşıma odaklıdır: REST API'yi çağırır ve sohbet yanıtlarını SSE üzerinden akıtır. Ham PII'yi kendi başına hiç görmez.

## Teknoloji yığını

- **Next.js 16** (App Router) + **React 19** + **TypeScript 5**
- **Tailwind CSS 3** — stil sayfası
- **Axios** — REST, **fetch + ReadableStream** — SSE sohbet
- **Jest + React Testing Library** — birim testleri

## Kurulum

```bash
cd packages/web
npm install
```

## Geliştirme

```bash
npm run dev        # webpack, 4 GB heap, port 3000
npm run build      # production build
npm run start      # production build'i sunar
npm run lint       # ESLint
npm test           # Jest
```

Depo kökünden `./dev.sh` bu paneli backend ile birlikte ayağa kaldırır.

## Dağıtım biçimleri

Panel, iki ortam değişkeniyle kontrol edilen iki topolojiyi destekler:

### 1. Tek container (varsayılan)

Frontend ve backend aynı origin'i paylaşır. `next.config.mjs` içindeki Next.js rewrite'ları `/api/*`, `/health`, `/metrics`, `/docs`, `/redoc` ve `/openapi.json` isteklerini backend süreçlerine yönlendirir.

```bash
# Build zamanı bir URL gerekmez — istekler göreli kalır.
npm run build && npm run start
```

- `NEXT_PUBLIC_API_BASE_URL` — **ayarlı değil.** Modül yüklendiğinde `baseURL` değeri `""` olur; Axios ve `fetch()` göreli yollarla çağrılır.
- `BACKEND_INTERNAL_URL` — build anında Next.js route manifest'ine gömülür. Yerel geliştirmede (`npm run dev`) başlangıçta ortamdan okunur; Docker build'lerinde build-arg olarak verilir (`docker/web.Dockerfile` dosyasına bakın). Varsayılan: `http://127.0.0.1:8000`.

### 2. Ayrık dağıtım

Panel ve API farklı origin'lerde çalışır (örneğin `app.septum.example` ↔ `api.septum.example`).

```bash
NEXT_PUBLIC_API_BASE_URL=https://api.septum.example npm run build
npm run start
```

- `NEXT_PUBLIC_API_BASE_URL` — build anında tarayıcı paketine yazılır. Sona eklenen slash karakterleri temizlenir; böylece çağıran kod `${baseURL}/api/...` biçiminde birleştirmeye devam edebilir (`src/lib/api.ts` içindeki `resolveBaseURL`).
- Backend, `FRONTEND_ORIGIN=https://app.septum.example` değişkeniyle başlatılmalıdır; aksi hâlde CORS izin listesi panelin origin'ine izin vermez. Çoklu origin için virgülle ayrılmış değer desteklenir (`FRONTEND_ORIGIN=https://app.example,https://admin.example`); `*` hoşgörülü joker moda karşılık gelir.

## Yapı

```
src/
├── app/          # Next.js App Router sayfaları (chat, documents, chunks, settings, setup wizard)
├── components/   # Feature bazında organize edilmiş durumsuz UI
├── hooks/        # Ortak React hook'ları
├── i18n/         # Çeviriler (İngilizce varsayılan + Türkçe)
├── lib/          # API istemcisi (axios), tipler, yardımcılar
└── store/        # Ortak state hook'ları (chat, documents, settings, regulations)
```

Tüm REST çağrıları `src/lib/api.ts` üzerinden geçer — bileşenlerde doğrudan `fetch`/`axios` kullanılmaz. Paylaşılan TypeScript arayüzleri `src/lib/types.ts` dosyasındadır.

## Lisans

MIT
