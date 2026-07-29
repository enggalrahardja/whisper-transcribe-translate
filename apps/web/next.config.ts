import path from "node:path";
import type { NextConfig } from "next";
import { securityHeaders } from "./security-headers.mjs";

const nextConfig: NextConfig = {
  distDir: process.env.NEXT_DIST_DIR ?? ".next",
  outputFileTracingRoot: path.resolve(__dirname, "../.."),
  turbopack: {
    root: path.resolve(__dirname, "../.."),
  },
  async headers() {
    return [{
      source: "/:path*",
      headers: securityHeaders(process.env.NODE_ENV),
    }];
  },
};

export default nextConfig;
