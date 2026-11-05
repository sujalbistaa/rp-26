import type { NextConfig } from "next";

const apiUrl = process.env.RKL_API_URL ?? "http://localhost:8100";

const config: NextConfig = {
  distDir: process.env.RKL_DIST_DIR ?? ".next",
  output: "standalone",
  poweredByHeader: false,
  devIndicators: false,
  reactStrictMode: true,
  // The default dev cache writes gigabytes to .next and filled this disk once.
  webpack(webpackConfig, { dev }) {
    if (dev) webpackConfig.cache = { type: "memory" };
    return webpackConfig;
  },
  // In production the edge proxy routes /api straight to the API. This covers local dev.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiUrl}/:path*` }];
  },
};

export default config;
