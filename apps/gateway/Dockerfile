# syntax=docker/dockerfile:1
FROM node:22-alpine AS base
RUN corepack enable
WORKDIR /repo

FROM base AS deps
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml* ./
COPY packages/shared/package.json packages/shared/
COPY apps/gateway/package.json apps/gateway/
RUN pnpm install --filter @verdict/gateway... --frozen-lockfile || \
    pnpm install --filter @verdict/gateway...

FROM deps AS build
COPY tsconfig.base.json ./
COPY packages/shared packages/shared
COPY apps/gateway apps/gateway
RUN pnpm --filter @verdict/shared build && pnpm --filter @verdict/gateway build

FROM base AS runtime
ENV NODE_ENV=production
COPY --from=build /repo/node_modules ./node_modules
COPY --from=build /repo/package.json ./
COPY --from=build /repo/packages/shared/dist packages/shared/dist
COPY --from=build /repo/packages/shared/package.json packages/shared/
COPY --from=build /repo/packages/shared/node_modules packages/shared/node_modules
COPY --from=build /repo/apps/gateway/dist apps/gateway/dist
COPY --from=build /repo/apps/gateway/package.json apps/gateway/
COPY --from=build /repo/apps/gateway/node_modules apps/gateway/node_modules
COPY config config
COPY artifacts artifacts

ENV MODELS_CONFIG_PATH=/repo/config/models.yaml

ARG GIT_SHA=unknown
ENV GIT_SHA=$GIT_SHA

USER node
EXPOSE 8080
CMD ["node", "apps/gateway/dist/index.js"]
