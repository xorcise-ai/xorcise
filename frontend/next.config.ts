import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "export",
  basePath: "/ui",
  trailingSlash: true,
  images: { unoptimized: true },
  // `next dev` otherwise writes AGENTS.md and CLAUDE.md into frontend/ on every run, leaving the
  // tree dirty (#126). The repo keeps its own agent guidance; whether to adopt AGENTS.md is #95.
  agentRules: false,
};

export default nextConfig;
