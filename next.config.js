/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  async rewrites() {
    // In PROD (one Vercel project), /api is served by the co-located Python
    // function via vercel.json's rewrite (/api → /api/index) — so Next adds no
    // rewrite. Only in local dev do we proxy /api + /static to a separately-run
    // FastAPI (uvicorn on :8000).
    if (process.env.NODE_ENV !== "development") return [];
    const apiUrl = process.env.API_URL || "http://localhost:8000";
    return [
      { source: "/api/:path*", destination: `${apiUrl}/api/:path*` },
      { source: "/static/:path*", destination: `${apiUrl}/static/:path*` },
    ];
  },
  images: {
    unoptimized: true,
  },
};

module.exports = nextConfig;
