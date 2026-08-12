# Fused per-mission base layer: docker:dind + compose + runner control.
#
# Each per-mission fused image (built locally by xorcise.core.runner.docker.build) is
# `FROM xorcise/mission-base`, with the mission's inner stack baked in as
# /mission/images.tar (loaded on boot — no inner pull at deploy). Local build only; the
# registry publish/sign cycle is not yet built.
#
# Tailscale is deliberately NOT installed here. The per-run network router runs as a SEPARATE
# inner container (the official tailscale/tailscale image, baked into images.tar) in its own
# clean netns — co-locating tailscaled in this outer netns next to the inner dockerd collides
# with kernel forwarding (the PoC's documented failure mode).
# Pinned, and deliberately >= 28. Engine <= 27 installs an OCI prestart hook that execs
# `/proc/<dockerd-pid>/exe`, which Rosetta cannot resolve — so on Apple Silicon every amd64
# mission container the inner daemon starts dies with "rosetta error: failed to open elf".
# Docker 28 removed the hook (moby#47406). Pinned rather than floating because that hook was
# removed in 26.0, RESTORED in moby#47646, then removed again in 28 — the property this relies
# on has flip-flopped before. 29.x is also the only branch still receiving security patches
# (28.x went EOL 2026-05-13). Keep NESTED_PROBE_IMAGE in runner/docker/rosetta.py in step with
# this line; tests/topology/test_dind_base_parity.py fails the build if they drift.
FROM docker:29.7.1-dind

# Base generation, inherited by every `FROM xorcise/mission-base` fused image. Only the MAJOR
# gates client compatibility (a major bump is breaking, e.g. this engine 27→29 move). MUST equal
# build.BASE_VERSION (asserted by tests/topology/test_dind_base_parity.py); bump BOTH together on
# a breaking base change so an artifact fused on an incompatible base is refused, not deployed.
LABEL ai.xorcise.base.version="2"

RUN apk add --no-cache iptables ca-certificates docker-cli-compose

COPY entrypoint.sh /usr/local/bin/xorcise-entrypoint.sh
RUN chmod +x /usr/local/bin/xorcise-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/xorcise-entrypoint.sh"]
