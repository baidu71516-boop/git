FROM node:22-alpine

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1

RUN corepack enable
WORKDIR /workspace

COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
COPY apps/web/package.json apps/web/package.json
RUN pnpm install --frozen-lockfile

COPY apps/web apps/web
RUN pnpm --filter @influencer-outreach/web build \
    && chown -R node:node /workspace

USER node
EXPOSE 3000
CMD ["pnpm", "--filter", "@influencer-outreach/web", "start"]

