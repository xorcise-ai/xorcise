# syntax=docker/dockerfile:1
# One target per role, all from a SINGLE base/package (no fork).
# Skeletal: NOT built in CI. The runner/headscale feature stories flesh these out.
FROM python:3.12-slim AS base
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -e .

FROM base AS all
RUN pip install --no-cache-dir -e ".[all]"
ENTRYPOINT ["xorcise", "serve", "--role", "all"]

FROM base AS control
ENTRYPOINT ["xorcise", "serve", "--role", "control"]

FROM base AS runner
RUN pip install --no-cache-dir -e ".[runner]"
ENTRYPOINT ["xorcise", "serve", "--role", "runner"]

FROM base AS headscale
ENTRYPOINT ["xorcise", "serve", "--role", "headscale"]

FROM base AS collector
ENTRYPOINT ["xorcise", "serve", "--role", "collector"]
