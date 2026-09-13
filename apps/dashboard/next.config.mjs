/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone", // small docker image; no node_modules copy
  reactStrictMode: true,
};

export default nextConfig;
