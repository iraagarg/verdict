/**
 * `output: "standalone"` is for the DOCKER image only, and must not be set
 * anywhere else.
 *
 * Standalone emits a self-contained server bundle into `.next/standalone`,
 * which is what `apps/dashboard/Dockerfile` copies so the image needs no
 * `node_modules`. Vercel builds from the ordinary `.next` layout and converts it
 * into its own output. Given a standalone build it finds nothing it recognises
 * and publishes an EMPTY deployment — reporting success, then returning
 * `x-vercel-error: NOT_FOUND` on every path including `/_next/static`.
 *
 * That is the third failure in this phase whose shape is "success that serves
 * nothing" (D-062, D-064, D-065), so the switch is explicit and named rather
 * than inferred from some ambient platform variable: the Dockerfile sets
 * DOCKER_BUILD=1, and only the Dockerfile does.
 *
 * @type {import('next').NextConfig}
 */
const nextConfig = {
  ...(process.env.DOCKER_BUILD === "1" ? { output: "standalone" } : {}),
  reactStrictMode: true,
};

export default nextConfig;
