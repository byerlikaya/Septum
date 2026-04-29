/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  outputFileTracingRoot: process.cwd(),
  // Disable compression to prevent SSE buffering — gzip holds bytes
  // until its internal buffer flushes, breaking real-time streaming.
  compress: false,
  // Raise the request-body cap that Next.js applies to /api/* rewrites.
  // Default is 10 MB, which silently truncates large document uploads
  // (audio recordings, scanned PDFs, image batches) and the partial
  // multipart body kills the backend connection with "socket hang up",
  // surfacing as a generic 500 to the user.
  //
  // The error message Next.js prints points at ``middlewareClientMaxBodySize``
  // but that key only governs Edge middleware. The actual key used by the
  // rewrite/proxy buffering layer is ``experimental.proxyClientMaxBodySize``
  // — verified by reading
  // ``next/dist/server/lib/router-utils/resolve-routes.js`` and
  // ``next/dist/server/next-server.js`` in the standalone build (line 1305:
  // ``const bodySizeLimit = this.nextConfig.experimental?.proxyClientMaxBodySize``).
  // The error-message URL in next.js itself has a ``TODO(jiwon): Update this
  // document link`` comment alongside it, confirming the link is stale.
  //
  // 500 MB matches the typical upper bound for the formats Septum supports
  // (Whisper audio + dense PDF scans). Bump if you regularly ingest larger
  // files.
  experimental: {
    proxyClientMaxBodySize: 500 * 1024 * 1024,
  },
  async rewrites() {
    const backend =
      process.env.BACKEND_INTERNAL_URL || "http://127.0.0.1:8000";
    return [
      { source: "/api/:path*", destination: `${backend}/api/:path*` },
      { source: "/health", destination: `${backend}/health` },
      { source: "/metrics", destination: `${backend}/metrics` },
      { source: "/docs", destination: `${backend}/docs` },
      {
        source: "/docs/oauth2-redirect",
        destination: `${backend}/docs/oauth2-redirect`,
      },
      { source: "/redoc", destination: `${backend}/redoc` },
      { source: "/openapi.json", destination: `${backend}/openapi.json` },
    ];
  },
  async headers() {
    // Defense-in-depth headers for the dashboard. Browsers ignore HSTS
    // over plain HTTP so the directive is safe to emit unconditionally.
    // CSP intentionally allows ``data:`` and ``blob:`` for in-browser
    // PDF / image previews of decrypted documents and ``'unsafe-inline'``
    // for Tailwind-injected styles; ``frame-ancestors 'none'`` blocks
    // clickjacking + ``connect-src 'self'`` keeps the dashboard from
    // beaconing to a different host even if a future dependency tries.
    //
    // Dev-mode caveats: Next.js + React HMR / fast-refresh use
    // ``eval()`` to reconstruct cross-realm callstacks and the dev
    // server pipes hot-update payloads over a WebSocket on the same
    // origin. Production never uses either, so the relaxations are
    // gated on ``NODE_ENV !== "production"``.
    const isDev = process.env.NODE_ENV !== "production";
    const scriptSrc = isDev
      ? "script-src 'self' 'unsafe-inline' 'unsafe-eval'"
      : "script-src 'self' 'unsafe-inline'";
    const connectSrc = isDev
      ? "connect-src 'self' ws: wss:"
      : "connect-src 'self'";
    const csp = [
      "default-src 'self'",
      scriptSrc,
      "style-src 'self' 'unsafe-inline'",
      "img-src 'self' data: blob:",
      "font-src 'self' data:",
      connectSrc,
      "frame-src 'self' blob:",
      "frame-ancestors 'none'",
      "base-uri 'self'",
      "form-action 'self'",
    ].join("; ");
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Content-Security-Policy", value: csp },
          {
            key: "Strict-Transport-Security",
            value: "max-age=31536000; includeSubDomains",
          },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          {
            key: "Permissions-Policy",
            value: "camera=(), microphone=(), geolocation=()",
          },
        ],
      },
    ];
  },
};

export default nextConfig;

