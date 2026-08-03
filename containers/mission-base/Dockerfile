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
FROM docker:27-dind

RUN apk add --no-cache iptables ca-certificates docker-cli-compose

COPY entrypoint.sh /usr/local/bin/xorcise-entrypoint.sh
RUN chmod +x /usr/local/bin/xorcise-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/xorcise-entrypoint.sh"]
