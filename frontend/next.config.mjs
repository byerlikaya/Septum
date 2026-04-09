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
};

export default nextConfig;

