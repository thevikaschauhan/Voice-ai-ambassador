# The agent worker as a Railway service.
#
# Outbound-only by design: it dials LiveKit Cloud, joins rooms and needs no
# public domain and no inbound routing (docs/01- Deployment; media transport
# stays on LiveKit Cloud, which Railway could not host anyway - no UDP ingress).
#
# ## This image mirrors CI, deliberately and exactly
#
# `.github/workflows/gates.yml` pins uv 0.10.7 and Python 3.14 and installs with
# `uv sync --frozen`. All three are load-bearing here for one reason:
# `agent/pyproject.toml` sets `exclude-newer = "5 days"`, a MOVING window, so an
# unfrozen resolve produces a different dependency set depending on the day it
# runs. An image that resolved its own tree would not be the tree the gates
# passed on. The uv base image pins the resolver and the interpreter in one
# layer rather than installing either at build time.
#
# `uv sync --frozen` keeps uv's default groups, which include `dev` (pytest).
# That is a few megabytes of test dependency in a runtime image, and it is the
# price of "identical to what CI verified" - the alternative, `--no-group dev`,
# is a different install than any gate ever ran.
#
# ## The build downloads the models
#
# `download-files` fetches what the plugins need - Silero's VAD weights above
# all - at BUILD time. `prewarm` loads Silero once per worker process, and a
# process that has to download it first pays that on the first call of a demo.
#
# ## Signals
#
# The worker drains on SIGTERM: it stops accepting jobs and lets running ones
# finish (LiveKit's own guidance for voice is a grace period generous enough for
# a conversation to end). So CMD is exec-form - a shell wrapper would swallow
# the signal and Railway would kill mid-call - and `--drain-timeout` is set
# explicitly rather than inheriting the framework's 3600s default, which would
# hold a deploy for an hour behind one stuck session. 600s is longer than any
# demo turn and short enough to redeploy behind.

# Debian trixie, not bookworm: bookworm has no python3.14 variant at this uv
# version (probed against the registry, not assumed).
FROM ghcr.io/astral-sh/uv:0.10.7-python3.14-trixie-slim

# Bytecode compiled at build time and no .pyc writes at runtime: a worker that
# compiles on first import pays it inside the first job.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies before source, so a code change does not re-resolve or
# re-download the tree. `--no-install-project` because the project itself is
# still missing at this point.
COPY agent/pyproject.toml agent/uv.lock ./agent/
RUN --mount=type=cache,target=/root/.cache/uv \
    cd agent && uv sync --frozen --no-install-project

# `data/` sits BESIDE `agent/`, not inside it, and that is not cosmetic: every
# loader resolves it as `Path(__file__).resolve().parents[3] / "data"` from
# `agent/src/<pkg>/`. Flattening the layout would make inventory, disclosures,
# fallbacks, the lexicon and the currency tables all fail to load at startup.
COPY data/ ./data/
COPY agent/ ./agent/

RUN --mount=type=cache,target=/root/.cache/uv \
    cd agent && uv sync --frozen

# Plugin model files, so the first call is not the one that downloads them.
RUN cd agent && uv run --no-sync python -m adapter.agent download-files

WORKDIR /app/agent

# The framework's own health server, and the reason there is no healthcheck in
# railway.json: in `start` mode it binds 8081 on all interfaces with `GET /`
# returning 200 (503 when the inference process is dead or the LiveKit
# connection has failed) and `GET /worker` returning worker JSON. The port is a
# fixed prod default in `WorkerOptions` with no CLI flag and no env var, so it
# cannot follow Railway's injected `PORT`. Exposed for anyone who wires a probe
# to 8081 directly; see the PR body for the one-line product change that would
# let it read `PORT` instead.
EXPOSE 8081

# Exec form, no shell: the worker must receive SIGTERM itself to drain.
CMD ["uv", "run", "--no-sync", "python", "-m", "adapter.agent", "start", "--drain-timeout", "600"]
