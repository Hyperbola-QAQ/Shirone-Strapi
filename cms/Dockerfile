# Build with the same major Node version as production. Override NODE_IMAGE with
# a verified local/registry mirror image when building inside the cluster.
ARG NODE_IMAGE=node:22-bookworm-slim
FROM ${NODE_IMAGE} AS build
WORKDIR /opt/app
ENV NPM_CONFIG_REGISTRY=https://registry.npmmirror.com/
COPY scripts/install-pinned-pnpm.sh /tmp/install-pinned-pnpm.sh
RUN sh /tmp/install-pinned-pnpm.sh && rm /tmp/install-pinned-pnpm.sh
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml .npmrc ./
RUN pnpm install --frozen-lockfile
COPY config ./config
COPY src ./src
COPY public ./public
RUN pnpm build

FROM ${NODE_IMAGE} AS runtime
WORKDIR /opt/app
RUN chown 2000:2000 /opt/app
COPY --from=build --chown=2000:2000 /opt/app /opt/app
ENV NODE_ENV=production HOME=/tmp
USER 2000:2000
EXPOSE 1337
CMD ["node", "node_modules/@strapi/strapi/bin/strapi.js", "start"]
