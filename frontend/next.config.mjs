/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  outputFileTracingRoot: process.cwd(),
  // Disable compression to prevent SSE buffering — gzip holds bytes
  // until its internal buffer flushes, breaking real-time streaming.
  compress: false,
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

