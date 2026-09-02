# The web service: the Next.js demo surface, built for Railway.
#
# WHY A DOCKERFILE AND NOT RAILWAY'S OWN BUILDER. Railway's builder can build a
# Next app from package.json, but it would be pointed at `web/` as the service
# root, and this app does not fit inside `web/`: `lib/inventory.ts` and
# `lib/readiness.ts` read `join(process.cwd(), '..', 'data', ...)` at REQUEST
# time, and `next.config.ts` sets `outputFileTracingRoot` to the repo root for
# exactly that reason. A `web/`-rooted build succeeds and then 500s on the first
# request that needs a price, which is the worst shape of failure: green build,
# broken stage. The layout below is therefore part of the contract, not an
# implementation detail - cwd is `/srv/web`, so `..` is `/srv`, and `/srv/data`
# is the same data the agent reads.
#
# SIZE, and the option not taken. This ships ~950MB: node:24-slim, the traced
# `.next` output and production `node_modules` (`next` plus its platform SWC
# binaries are 286MB of that). `output: 'standalone'` would cut it to roughly a
# third, and it is deliberately NOT used here: it relocates the whole app tree
# under `.next/standalone`, and the one thing this service must not get wrong is
# the `cwd/../data` layout below. It also replaces `npm run start` with `node
# server.js`, moving the NODE_ENV pin off the script that exists to hold it. A
# slower deploy is a cost; a subtler layout around a load-bearing relative path
# is a defect waiting for a future edit. Revisit if deploy time hurts.
#
# Node is pinned rather than floating. AGENTS.md's rule for the agent image is
# that CI and the container run the same interpreter; the web side had no pin at
# all before this, and an image that silently changes major versions is not a
# build, it is a coin toss. 24 is the active LTS.
FROM node:24-slim AS deps

WORKDIR /srv/web
COPY web/package.json web/package-lock.json ./
# --ignore-scripts is deliberate and safe HERE: the only package in this tree
# with a meaningful install script is `sharp`, which Next uses to optimise
# `next/image`, and this app has no `next/image` anywhere. Skipping scripts
# keeps the build hermetic and reproducible; if an `<Image>` ever lands, drop
# this flag and pin `sharp` explicitly rather than letting it half-install.
RUN npm ci --ignore-scripts


# The runtime needs only production dependencies. Resolved from the same
# lockfile in its own stage rather than pruned in place, so the builder keeps a
# complete tree to compile against and the runner gets a tree with no compiler,
# no test runner and no linter in it.
FROM node:24-slim AS prod-deps

WORKDIR /srv/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts


FROM node:24-slim AS builder

WORKDIR /srv/web
COPY --from=deps /srv/web/node_modules ./node_modules
COPY web ./
# `next build` does not read data/ - every route that does is dynamic (`ƒ`) and
# renders on demand - so the data lands in the runtime stage only.
# NODE_ENV is pinned by the npm script itself, and that is load-bearing: an
# inherited NODE_ENV=development makes `next build` prerender /404 from the dev
# pages-router error component and fail on `<Html>` (README, AGENTS.md
# 2026-08-31). Calling the script rather than `next build` keeps that pin.
RUN npm run build


FROM node:24-slim AS runner

# Next writes nothing at runtime here, and a non-root runtime is the default
# posture for a public service. The node image ships this user already.
USER node
WORKDIR /srv/web

ENV NODE_ENV=production
# Railway injects PORT and expects the process to bind it on all interfaces.
# `next start` reads PORT and binds 0.0.0.0 already (verified: it listened on
# *:7311 under PORT=7311), so this is the local default, not an override.
ENV PORT=3000

# The runtime tree, and nothing else.
COPY --from=prod-deps --chown=node:node /srv/web/node_modules ./node_modules
# `next start` LOADS next.config.ts, and a TypeScript config needs a TypeScript
# compiler to read. Without one Next does not fail - it tries to INSTALL
# typescript at boot, with yarn, over the network, into the running container:
#
#   ⚠ Installing TypeScript as it was not found while loading "next.config.ts".
#   Installing devDependencies (yarn): - typescript@5.8.2
#
# That is a production hazard three ways over - it needs egress at boot, it
# writes into the image at runtime (and fails as a non-root user, which is how
# this was found), and the version it reaches for is unpinned and NOT the 5.9.2
# this repo pins. So the compiler ships, from the same lockfile the build used.
# One dependency-free directory, ~23MB, and no second source of truth for the
# version. The alternative is renaming the config to .mjs, which is left alone
# deliberately: it would trade a real `NextConfig` type for a JSDoc comment on
# a file three services read.
COPY --from=deps --chown=node:node /srv/web/node_modules/typescript ./node_modules/typescript
COPY --from=builder --chown=node:node /srv/web/.next ./.next
COPY --chown=node:node web/package.json web/next.config.ts ./
# The sibling `data/` the request path reads. Copied last because it is the
# layer most likely to change on a demo day.
COPY --chown=node:node data /srv/data

EXPOSE 3000
# The npm script, again for the NODE_ENV pin.
CMD ["npm", "run", "start"]
