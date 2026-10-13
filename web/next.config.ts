import type { NextConfig } from "next";
import { PHASE_DEVELOPMENT_SERVER } from "next/constants";

export default function nextConfig(phase: string): NextConfig {
  if (phase !== PHASE_DEVELOPMENT_SERVER) {
    // Production remains a static export behind the existing Nginx Basic Auth.
    return { output: "export", trailingSlash: true };
  }

  let origin: URL;
  try {
    origin = new URL(process.env.VISUAL_DEV_API_ORIGIN || "http://127.0.0.1:8002");
  } catch {
    throw new Error("VISUAL_DEV_API_ORIGIN must be a local HTTP origin without credentials or path");
  }
  if (origin.protocol !== "http:" || !["127.0.0.1", "localhost"].includes(origin.hostname)
    || origin.username || origin.password || origin.pathname !== "/" || origin.search || origin.hash) {
    throw new Error("VISUAL_DEV_API_ORIGIN must be a local HTTP origin without credentials or path");
  }
  return {
    trailingSlash: true,
    // Preserve the FastAPI path exactly; POST must not redirect back to port 8002.
    skipTrailingSlashRedirect: true,
    experimental: { proxyTimeout: 240_000 },
    async rewrites() {
      return [{ source: "/api/visual-search/:path*", destination: `${origin.origin}/api/visual-search/:path*` }];
    },
  };
}
