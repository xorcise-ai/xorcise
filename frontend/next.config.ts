import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "export",
  basePath: "/ui",
  trailingSlash: true,
  images: { unoptimized: true },
};

export default nextConfig;
