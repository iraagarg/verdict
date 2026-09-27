# syntax=docker/dockerfile:1
FROM node:22-alpine AS base
RUN corepack enable
WORKDIR /repo

FROM base AS deps
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml* ./
COPY apps/dashboard/package.json apps/dashboard/
RUN pnpm install --filter @verdict/dashboard... --frozen-lockfile || \
    pnpm install --filter @verdict/dashboard...

FROM deps AS build
COPY apps/dashboard apps/dashboard
# The dashboard reads committed artifacts at BUILD time and bakes them into the
# static output (D-052). Without this the image builds cleanly and every page
# renders its empty state, which looks identical to "no measurement has run yet"
# — a silent failure the container cannot report.
COPY artifacts artifacts
ENV NEXT_TELEMETRY_DISABLED=1
# Selects `output: "standalone"` in next.config.mjs. ONLY this build sets it:
# Vercel needs the ordinary .next layout and silently publishes an empty site
# when given a standalone build (D-065).
ENV DOCKER_BUILD=1
RUN pnpm --filter @verdict/dashboard build

FROM node:22-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
COPY --from=build /repo/apps/dashboard/.next/standalone ./
COPY --from=build /repo/apps/dashboard/.next/static ./apps/dashboard/.next/static
USER node
EXPOSE 3000
CMD ["node", "apps/dashboard/server.js"]
