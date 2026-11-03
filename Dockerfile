# Override NODE_IMAGE with the equivalent verified Node image in another registry.
ARG NODE_IMAGE=192.168.86.100:30500/ca-clean/node@sha256:4de898ca0d4e1f2169d9b846df3710f29f6787fd3131211e2e74a7898c0528cc
FROM ${NODE_IMAGE} AS build
WORKDIR /app
ENV NPM_CONFIG_REGISTRY=https://registry.npmmirror.com/ \
    PNPM_CONFIG_REGISTRY=https://registry.npmmirror.com/
COPY cms/scripts/install-pinned-pnpm.sh /tmp/install-pinned-pnpm.sh
RUN sh /tmp/install-pinned-pnpm.sh && rm /tmp/install-pinned-pnpm.sh
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml .npmrc ./
COPY patches ./patches
RUN --mount=type=cache,target=/root/.local/share/pnpm/store pnpm install --frozen-lockfile
COPY . .
RUN pnpm build

FROM ${NODE_IMAGE} AS runtime
WORKDIR /app
ENV NODE_ENV=production HOST=0.0.0.0 PORT=4321
COPY --from=build --chown=node:node /app/dist ./dist
COPY --from=build --chown=node:node /app/shirones/config/FooterConfig.html ./shirones/config/FooterConfig.html
# pnpm's symlinked packages require the entire node_modules virtual store.
COPY --from=build --chown=node:node /app/node_modules ./node_modules
USER node
EXPOSE 4321
CMD ["node", "dist/server/entry.mjs"]
