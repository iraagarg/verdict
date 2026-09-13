import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["src/**/*.test.ts"],
    environment: "node",
    // The route tests listen on a real socket and drive a real mock provider
    // over HTTP, which is slower than the 5s default — and deliberately so:
    // only a real listener reproduces a client disconnecting mid-stream.
    testTimeout: 20_000,
    hookTimeout: 20_000,
  },
});
