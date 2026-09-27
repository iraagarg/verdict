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

# Deliberately NOT `FROM base`: the runtime image must not carry a package
# manager. `base` runs `corepack enable`, and the root package.json names
# `packageManager: pnpm@...`, so ANY stray npm/pnpm invocation at container
# start makes corepack download pnpm and pnpm then tries to write a lockfile
# into /repo — which root owns and the `node` user cannot write. On Railway
# that produced ERR_PNPM_LOCKFILE_WRITE_FILE in an endless restart loop, with
# the build itself perfectly green.
#
# A runtime image needs one thing: node. Nothing here should ever resolve a
# dependency — that already happened in the build stage.
FROM node:22-alpine AS runtime
WORKDIR /repo
ENV NODE_ENV=production

# --chown on every copy, so the process user owns what it reads. Without it
# everything lands as root and the app runs as `node`, which is the second half
# of the failure above.
COPY --from=build --chown=node:node /repo/node_modules ./node_modules
COPY --from=build --chown=node:node /repo/package.json ./
COPY --from=build --chown=node:node /repo/packages/shared/dist packages/shared/dist
COPY --from=build --chown=node:node /repo/packages/shared/package.json packages/shared/
COPY --from=build --chown=node:node /repo/packages/shared/node_modules packages/shared/node_modules
COPY --from=build --chown=node:node /repo/apps/gateway/dist apps/gateway/dist
COPY --from=build --chown=node:node /repo/apps/gateway/package.json apps/gateway/
COPY --from=build --chown=node:node /repo/apps/gateway/node_modules apps/gateway/node_modules
COPY --chown=node:node config config
COPY --chown=node:node artifacts artifacts

ENV MODELS_CONFIG_PATH=/repo/config/models.yaml

ARG GIT_SHA=unknown
ENV GIT_SHA=$GIT_SHA

USER node
EXPOSE 8080
CMD ["node", "apps/gateway/dist/index.js"]
