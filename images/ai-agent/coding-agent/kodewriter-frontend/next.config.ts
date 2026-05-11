import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: 'standalone',
  basePath: '/agent',
  assetPrefix: '/agent',
};

export default nextConfig;
